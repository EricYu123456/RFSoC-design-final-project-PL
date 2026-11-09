`timescale 1ns/1ps
// Digital model of the external RF path, not an RFDC transistor simulation.
// Uses unknown cable delay, quadrature phase, noise and an erased ACK.
module tb_rf_board;
    parameter integer PACKETS=3;
    parameter integer CHANNEL_DELAY=37;
    parameter integer DROP_ACK=0;
    parameter integer DISCONNECT=0;
    localparam integer BYTES=PACKETS*1024;
    reg clk=0;
    always #3.255208333 clk=~clk;
    reg aresetn=0;
    reg [31:0] control=32'h04004001;
    wire [31:0] status,levels,retries;
    wire [3:0] leds;
    reg [31:0] adc_i=0,adc_q=0;
    wire adc_i_ready,adc_q_ready;
    wire [63:0] dac_data;
    wire dac_valid;
    wire src_clk,src_rst,src_en,dst_clk,dst_rst,dst_en;
    wire [3:0] src_we,dst_we;
    wire [31:0] src_addr,src_din,dst_addr,dst_din;
    reg [31:0] src_dout=0;
    reg [31:0] source [0:BYTES/4-1];
    reg [31:0] destination [0:BYTES/4-1];
    reg [CHANNEL_DELAY:0] cable=0;
    reg [15:0] noise=16'hace1;
    reg ack_seen=0,drop_active=0;
    wire ack_busy=dut.link.ack_tx.busy;
    integer i,lane,cycles=0;
    reg [7:0] pixels [0:307199];
    ook_rf_board #(.FRAME_BYTES(BYTES)) dut (
        .clk(clk),.aresetn(aresetn),.control(control),.status(status),.levels(levels),.retries(retries),.leds(leds),
        .adc_i(adc_i),.adc_q(adc_q),.adc_i_valid(1'b1),.adc_q_valid(1'b1),
        .adc_i_ready(adc_i_ready),.adc_q_ready(adc_q_ready),.dac_data(dac_data),.dac_valid(dac_valid),.dac_ready(1'b1),
        .src_clk(src_clk),.src_rst(src_rst),.src_en(src_en),.src_we(src_we),.src_addr(src_addr),.src_din(src_din),.src_dout(src_dout),
        .dst_clk(dst_clk),.dst_rst(dst_rst),.dst_en(dst_en),.dst_we(dst_we),.dst_addr(dst_addr),.dst_din(dst_din),.dst_dout(32'd0));
    always @(posedge clk) begin
        cycles<=cycles+1;
        noise<={noise[14:0],noise[15]^noise[13]^noise[12]^noise[10]};
        if (src_en) begin
            if (src_addr>=BYTES || src_addr[1:0]!=0 || src_we!=0) $fatal(1,"Bad source BRAM request");
            src_dout<=source[src_addr>>2];
        end
        if (dst_en) begin
            if (dst_addr>=BYTES || dst_addr[1:0]!=0) $fatal(1,"Bad sink BRAM request");
            for (lane=0;lane<4;lane=lane+1)
                if (dst_we[lane]) destination[dst_addr>>2][8*lane+:8]<=dst_din[8*lane+:8];
        end
        if (ack_busy && !ack_seen && DROP_ACK) begin ack_seen<=1; drop_active<=1; end
        if (!ack_busy) drop_active<=0;
        cable<={cable[CHANNEL_DELAY-1:0],dac_valid && dac_data[15:0]!=0 && !drop_active && !DISCONNECT};
        // A 1 k sample phasor and 20 LSB noise exercise threshold margins.
        if (cable[CHANNEL_DELAY]) begin
            adc_i<={16'd2000+{12'd0,noise[3:0]},16'd2000+{12'd0,noise[7:4]}};
            adc_q<={-16'd1300+{12'd0,noise[11:8]},-16'd1300+{12'd0,noise[15:12]}};
        end else begin
            adc_i<={{12'd0,noise[3:0]},{12'd0,noise[7:4]}};
            adc_q<={{12'd0,noise[11:8]},{12'd0,noise[15:12]}};
        end
        if (status[5:4]!=0) $fatal(1,"Board transport/address fault %h",status);
    end
    initial begin
        $readmemh("assets/image.mem",pixels);
        for (i=0;i<BYTES/4;i=i+1) begin
            source[i]={pixels[4*i+3],pixels[4*i+2],pixels[4*i+1],pixels[4*i]};
            destination[i]=32'hdeadbeef;
        end
        repeat(20) @(negedge clk);
        aresetn=1;
        repeat(20) @(negedge clk);
        control[0]=0;
        repeat(100) @(negedge clk);
        control[1]=1;
        repeat(4) @(negedge clk);
        control[1]=0;
        wait(status[0] || status[1]);
        repeat(10) @(negedge clk);
        if (DISCONNECT) begin
            if (!status[1] || status[0]) $fatal(1,"Disconnected channel falsely succeeded");
            $display("RF_SIM_PASS disconnected channel fails safely retries=%0d",retries);
        end else begin
            if (!status[0] || status[1] || !status[3]) $fatal(1,"RF transfer failed status=%h",status);
            if (status[24:6]!=BYTES) $fatal(1,"Incorrect byte count %0d",status[24:6]);
            for (i=0;i<BYTES/4;i=i+1)
                if (source[i]!==destination[i]) $fatal(1,"Image mismatch at word %0d",i);
            if (retries != DROP_ACK) $fatal(1,"Unexpected retries %0d",retries);
            $display("RF_SIM_PASS packets=%0d bytes=%0d mismatch=0 delay=%0d retries=%0d cycles=%0d",PACKETS,BYTES,CHANNEL_DELAY,retries,cycles);
        end
        $finish;
    end
    initial begin
        repeat((PACKETS+12)*600000+2000000) @(posedge clk);
        $fatal(1,"RF simulation watchdog expired");
    end
endmodule

// Packet BRAM has exactly one synchronous read stage, as in the generated IP.
`ifdef IVERILOG
module ip_packet_ram (
    input wire clka,ena,wea,input wire [9:0] addra,input wire [7:0] dina,
    input wire clkb,enb,input wire [9:0] addrb,output reg [7:0] doutb);
    reg [7:0] mem [0:1023];
    always @(posedge clka) if (ena && wea) mem[addra]<=dina;
    always @(posedge clkb) if (enb) doutb<=mem[addrb];
endmodule
`endif
