`timescale 1ns/1ps
module ofdm_rf_board # (parameter integer FRAME_BYTES=307200, parameter integer WINDOW=8) (
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER="XIL_INTERFACENAME clk, ASSOCIATED_BUSIF ADC_I:ADC_Q:DAC, ASSOCIATED_RESET aresetn, FREQ_HZ 153600000" *)
    input wire clk,
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER="XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    input wire [31:0] control, output wire [31:0] status,
    output wire [31:0] levels, output wire [31:0] retries, output wire [3:0] leds,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_I TDATA" *) input wire [31:0] adc_i,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_I TVALID" *) input wire adc_i_valid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_I TREADY" *) output wire adc_i_ready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_Q TDATA" *) input wire [31:0] adc_q,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_Q TVALID" *) input wire adc_q_valid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 ADC_Q TREADY" *) output wire adc_q_ready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 DAC TDATA" *) output wire [63:0] dac_data,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 DAC TVALID" *) output wire dac_valid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 DAC TREADY" *) input wire dac_ready,
    (* X_INTERFACE_PARAMETER="XIL_INTERFACENAME SOURCE, MASTER_TYPE BRAM_CTRL, MEM_SIZE 524288, MEM_WIDTH 32, READ_LATENCY 1" *)
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE CLK" *) output wire src_clk,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE RST" *) output wire src_rst,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE EN" *) output wire src_en,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE WE" *) output wire [3:0] src_we,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE ADDR" *) output wire [31:0] src_addr,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE DIN" *) output wire [31:0] src_din,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SOURCE DOUT" *) input wire [31:0] src_dout,
    (* X_INTERFACE_PARAMETER="XIL_INTERFACENAME SINK, MASTER_TYPE BRAM_CTRL, MEM_SIZE 524288, MEM_WIDTH 32, READ_LATENCY 1" *)
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK CLK" *) output wire dst_clk,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK RST" *) output wire dst_rst,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK EN" *) output wire dst_en,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK WE" *) output wire [3:0] dst_we,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK ADDR" *) output wire [31:0] dst_addr,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK DIN" *) output wire [31:0] dst_din,
    (* X_INTERFACE_INFO="xilinx.com:interface:bram:1.0 SINK DOUT" *) input wire [31:0] dst_dout
);
    wire rst=!aresetn || control[0], receiver=control[3];
    wire [15:0] amplitude={2'b0,control[15:4],2'b0};
    reg previous_start, stream_fault;
    wire start=control[1] && !previous_start && !control[2];
    reg [2:0] descriptor;
    reg [31:0] tx_session;
    reg [15:0] tx_frame, tx_count;
    wire [18:0] address, sink_addr;
    reg [1:0] source_lane;
    wire [7:0] source_byte=src_dout>>(source_lane*8);
    wire [7:0] sink_byte;
    wire sink_we,done,failed,busy,underrun;
    wire [15:0] missing,active_frame,active_count;
    wire [31:0] active_session;
    wire [7:0] tx_byte,rx_byte;
    wire tx_valid,tx_last,tx_ready,tx_done,rx_start,rx_valid;
    reg [1:0] metadata;
    always @(posedge clk) begin
        source_lane<=address[1:0];
        if(rst) begin previous_start<=0; descriptor<=0; stream_fault<=0; metadata<=0; end
        else begin
            previous_start<=control[1];
            if(start && !receiver) descriptor<=1;
            else if(descriptor!=0) descriptor<=descriptor==4 ? 0 : descriptor+1'b1;
            if(descriptor==2) tx_session<=src_dout;
            if(descriptor==3) {tx_count,tx_frame}<=src_dout;
            if(busy && (!dac_ready || !adc_i_valid || !adc_q_valid || underrun)) stream_fault<=1;
            if(!done || !receiver) metadata<=0;
            else if(metadata<2) metadata<=metadata+1'b1;
        end
    end
    assign src_clk=clk; assign dst_clk=clk; assign src_rst=!aresetn; assign dst_rst=!aresetn;
    assign src_en=!rst && !receiver; assign src_we=0; assign src_din=0;
    assign src_addr=descriptor==1 ? 32'h7fff0 : descriptor==2 ? 32'h7fff4 : {13'b0,address[18:2],2'b0};
    wire meta_we=receiver && done && metadata<2;
    assign dst_en=!rst && (sink_we || meta_we);
    assign dst_addr=meta_we ? (metadata==0 ? 32'h7fff0 : 32'h7fff4) : {13'b0,sink_addr[18:2],2'b0};
    assign dst_we=meta_we ? 4'hf : (sink_we ? 4'b1<<sink_addr[1:0] : 0);
    assign dst_din=meta_we ? (metadata==0 ? active_session : {active_count,active_frame}) : {4{sink_byte}};
    assign adc_i_ready=1; assign adc_q_ready=1; assign dac_valid=aresetn;
    wire host_done=done && (!receiver || metadata==2);
    assign status={7'h42,3'b0,missing,1'b0,stream_fault,host_done && receiver,busy,failed,host_done};
    assign leds={failed,stream_fault,busy,host_done};
    air_link #(.PACKETS(FRAME_BYTES/1024),.WINDOW(WINDOW)) link(.clk(clk),.rst(rst),.receiver(receiver),
        .start(receiver ? start : descriptor==4),.local_session(tx_session),.local_frame(tx_frame),.local_count(tx_count),
        .source_address(address),.source_byte(source_byte),.write_enable(sink_we),.write_address(sink_addr),.write_data(sink_byte),
        .tx_byte(tx_byte),.tx_valid(tx_valid),.tx_last(tx_last),.tx_ready(tx_ready),.tx_done(tx_done),
        .rx_start(rx_start),.rx_valid(rx_valid),.rx_byte(rx_byte),.done(done),.failed(failed),.busy(busy),.retries(retries),
        .missing(missing),.session(active_session),.frame(active_frame),.count(active_count));
    air_modem modem(.clk(clk),.rst(rst),.pilot(control[2]),.amplitude(amplitude),.threshold(control[31:16]),
        .dac(dac_data),.adc_i(adc_i),.adc_q(adc_q),.adc_valid(adc_i_valid && adc_q_valid),
        .tx_byte(tx_byte),.tx_valid(tx_valid),.tx_last(tx_last),.tx_ready(tx_ready),.tx_done(tx_done),
        .rx_start(rx_start),.rx_valid(rx_valid),.rx_byte(rx_byte),.levels(levels),.underrun(underrun));
endmodule
