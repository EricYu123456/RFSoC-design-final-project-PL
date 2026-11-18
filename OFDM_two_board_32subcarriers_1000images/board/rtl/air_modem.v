`timescale 1ns/1ps
module air_modem #(parameter ACK_BPSK=1) (
    input wire clk, rst, pilot, receiver, input wire [15:0] amplitude, threshold,
    output reg [63:0] dac, input wire [31:0] adc_i, adc_q, input wire adc_valid,
    input wire [7:0] tx_byte, input wire tx_valid, tx_last, output wire tx_ready, tx_done,
    output wire rx_start, rx_valid, output wire [7:0] rx_byte,
    output wire [31:0] levels, output wire underrun
);
    wire signed [12:0] ti,tq;
    wire busy;
    ofdm_tx tx(.bpsk(receiver && ACK_BPSK!=0),.clk(clk),.rst(rst),.s_data(tx_byte),.s_valid(tx_valid),.s_last(tx_last),
        .s_ready(tx_ready),.sample_i(ti),.sample_q(tq),.busy(busy),.frame_done(tx_done),.underrun(underrun));
    // Smooth DAC transitions across the eight fabric clocks per baseband sample.
    // A four-tap moving average retains headroom and has linear phase.
    reg signed [12:0] i1,i2,i3,q1,q2,q3;
    reg signed [14:0] isum,qsum;
    reg signed [30:0] iprod,qprod;
    wire signed [15:0] gain=amplitude;
    wire [15:0] out_i=iprod >>> 13, out_q=qprod >>> 13;
    reg [7:0] mute;
    always @(posedge clk) begin
        i1<=ti; i2<=i1; i3<=i2; q1<=tq; q2<=q1; q3<=q2;
        isum<=ti+i1+i2+i3; qsum<=tq+q1+q2+q3;
        iprod<=isum*gain; qprod<=qsum*gain;
        if(rst) dac<=0;
        else if(pilot) dac<={16'b0,amplitude,16'b0,amplitude};
        else dac<={out_q,out_i,out_q,out_i};
        if(rst || busy || pilot) mute<=255; else if(mute!=0) mute<=mute-1'b1;
    end
    // Average both RFDC lanes before decimation. Energy telemetry stays live
    // throughout pilot calibration, while self-transmissions cannot enter the parser.
    wire signed [16:0] si=$signed(adc_i[15:0])+$signed(adc_i[31:16]);
    wire signed [16:0] sq=$signed(adc_q[15:0])+$signed(adc_q[31:16]);
    wire frame_start, byte_valid;
    ofdm_rx rx(.bpsk(!receiver && ACK_BPSK!=0),.clk(clk),.rst(rst),.adc_valid(adc_valid && mute==0),
        .adc_i({2{si[16:1]}}),.adc_q({2{sq[16:1]}}),.threshold(threshold),
        .frame_start(frame_start),.byte_valid(byte_valid),.byte_data(rx_byte),
        .magnitude(levels[15:0]),.peak(levels[31:16]));
    assign rx_start=frame_start && mute==0;
    assign rx_valid=byte_valid && mute==0;
endmodule
