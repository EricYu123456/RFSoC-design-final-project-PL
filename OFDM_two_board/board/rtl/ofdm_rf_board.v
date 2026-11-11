`timescale 1ns/1ps
module ofdm_rf_board # (parameter integer FRAME_BYTES=307200) (
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
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 DAC TDATA" *) output reg [63:0] dac_data,
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
    wire rst = !aresetn || control[0];
    wire receiver_role = control[3];
    wire pilot = control[2];
    wire rx_armed = receiver_role && !rst && !pilot;
    reg start_d, sending;
    wire start = control[1] && !start_d && !rst && !receiver_role && !pilot;
    localparam [15:0] PACKET_COUNT=FRAME_BYTES/1024;
    reg [18:0] source_index;
    reg [1:0] read_state;
    reg [7:0] image_data;
    reg stream_fault, address_fault;
    reg [25:0] heartbeat;
    wire image_ready, sink_valid, sink_quality, sender_done, failed, phy_fault;
    wire [15:0] rx_present_packets, tx_exhausted_packets;
    wire [18:0] received_bytes = {rx_present_packets[8:0],10'b0};
    wire [18:0] result_count = receiver_role ? received_bytes : {3'b0,tx_exhausted_packets};
    wire rx_complete = receiver_role && received_bytes == FRAME_BYTES;
    wire done = receiver_role ? rx_complete : sender_done;
    wire active = sending || rx_armed;
    wire [7:0] sink_data;
    wire [18:0] sink_addr;
    wire [15:0] magnitude, peak;
    wire adc_valid = adc_i_valid && adc_q_valid;
    wire image_valid = sending && read_state==2;
    assign adc_i_ready=1'b1;
    assign adc_q_ready=1'b1;
    assign dac_valid=aresetn;
    // I,Q,I,Q: two complex input samples per beat, 1/8 full scale by default.
    wire [15:0] amplitude = {2'b0,control[15:4],2'b0};
    wire signed [12:0] tx_i, tx_q;
    wire signed [15:0] gain = $signed(amplitude);
    reg signed [28:0] scaled_i, scaled_q;
    wire signed [15:0] dac_i = scaled_i >>> 11;
    wire signed [15:0] dac_q = scaled_q >>> 11;
    always @(posedge clk) begin
        scaled_i <= tx_i * gain;
        scaled_q <= tx_q * gain;
        if (rst) dac_data <= 0;
        else if (dac_ready) begin
            if (pilot) dac_data <= {16'd0,amplitude,16'd0,amplitude};
            else if (active) dac_data <= {dac_q,dac_i,dac_q,dac_i};
            else dac_data <= 0;
        end
    end
    assign src_clk=clk; assign dst_clk=clk;
    assign src_rst=!aresetn; assign dst_rst=!aresetn;
    assign src_en=sending && read_state==0 && source_index<FRAME_BYTES;
    assign src_we=4'b0; assign src_din=32'd0;
    assign src_addr={13'd0,source_index[18:2],2'b0};
    // Payload slots are indexed by packet ID. Keep quality bytes after the image.
    wire invalid_sink_addr = sink_quality ? sink_addr>=PACKET_COUNT : sink_addr>=FRAME_BYTES;
    wire [18:0] write_addr = sink_quality ? FRAME_BYTES + sink_addr : sink_addr;
    assign dst_en=sink_valid && rx_armed && !invalid_sink_addr;
    assign dst_we=dst_en ? (4'b0001<<write_addr[1:0]) : 4'b0;
    assign dst_addr={13'd0,write_addr[18:2],2'b0};
    assign dst_din={4{sink_data}};
    assign levels={peak,magnitude};
    assign status={7'h3f,result_count,address_fault,stream_fault|phy_fault,
                   rx_complete,active,failed,done};
    assign leds={failed|stream_fault|phy_fault|address_fault,done,active,heartbeat[25]};
    always @(posedge clk) begin
        if (!aresetn) heartbeat<=0; else heartbeat<=heartbeat+1'b1;
        if (rst) begin
            start_d<=0; sending<=0; source_index<=0;
            read_state<=0; image_data<=0; stream_fault<=0; address_fault<=0;
        end else begin
            start_d<=control[1];
            if (start) sending<=1;
            if (sender_done || failed) sending<=0;
            if (active && (!dac_ready || !adc_valid)) stream_fault<=1;
            if (src_en) read_state<=1;
            else if (read_state==1) begin
                image_data<=src_dout>>(source_index[1:0]*8); read_state<=2;
            end else if (image_valid && image_ready) begin
                source_index<=source_index+1'b1; read_state<=0;
            end
            if (sink_valid && rx_armed && invalid_sink_addr) address_fault<=1;
        end
    end
    rf_image_link link (.clk(clk),.rst(rst),.start(start),
        .receiver_role(receiver_role),.listen(active),
        .total_packets(PACKET_COUNT),.image_data(image_data),.image_valid(image_valid),
        .image_ready(image_ready),.sink_data(sink_data),.sink_addr(sink_addr),.sink_valid(sink_valid),.sink_quality(sink_quality),
        .sink_ready(1'b1),.adc_i(adc_i),.adc_q(adc_q),.adc_valid(adc_valid),
        .threshold(control[31:16]),.tx_i(tx_i),.tx_q(tx_q),.done(sender_done),.failed(failed),
        .magnitude(magnitude),.peak(peak),.retransmissions(retries),.phy_fault(phy_fault),
        .rx_present_packets(rx_present_packets),.tx_exhausted_packets(tx_exhausted_packets));
endmodule

