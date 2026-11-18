`timescale 1ns/1ps
// Simulation-only: a reliable byte channel with the same backpressure,
// burst start and end-of-transmission interface as the OFDM modem.
module air_modem #(parameter ACK_BPSK=1)(input clk,rst,pilot,receiver,input [15:0] amplitude,threshold,
    output reg [63:0] dac,input [31:0] adc_i,adc_q,input adc_valid,
    input [7:0] tx_byte,input tx_valid,tx_last,output tx_ready,output reg tx_done,
    output rx_start,rx_valid,output [7:0] rx_byte,output [31:0] levels,output underrun);
    reg active;
    assign tx_ready=active;
    assign {rx_start,rx_valid,rx_byte}=adc_i[9:0];
    assign levels=0; assign underrun=0;
    always @(posedge clk) begin
        dac<=0; tx_done<=0;
        if(rst) active<=0;
        else if(tx_valid && !active) begin dac<=512; active<=1; end
        else if(tx_valid && active) begin
            dac<={54'b0,1'b0,1'b1,tx_byte};
            if(tx_last) begin active<=0; tx_done<=1; end
        end
    end
endmodule

