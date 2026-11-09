`timescale 1ns/1ps
// ADC streams contain two I samples and two Q samples per fabric clock.
// Magnitude removes the unknown RF phase. Hysteresis and edge qualification
// recover symbol phase from the preamble; no TX timing enters this receiver.
module rf_ook_rx # (parameter integer SYMBOL_CLOCKS=64) (
    input wire clk, input wire rst,
    input wire [31:0] adc_i, input wire [31:0] adc_q, input wire adc_valid,
    input wire [15:0] threshold,
    output reg bit_data, output reg bit_valid,
    output reg [15:0] magnitude, output reg [15:0] peak
);
    function [15:0] abs16;
        input [15:0] x;
        begin abs16 = x[15] ? (~x+16'd1) : x; end
    endfunction
    wire [17:0] mag_sum = {2'b0,abs16(adc_i[15:0])}+{2'b0,abs16(adc_i[31:16])}
                         +{2'b0,abs16(adc_q[15:0])}+{2'b0,abs16(adc_q[31:16])};
    reg [18:0] filter_acc;
    wire [15:0] filtered = filter_acc[18:3];
    wire [16:0] high_threshold = {1'b0,threshold}+{4'b0,threshold[15:3]};
    wire [15:0] low_threshold = threshold-(threshold>>3);
    reg level, candidate;
    reg [2:0] stable;
    reg [15:0] phase;
    wire proposed = level ? filtered > low_threshold : {1'b0,filtered} > high_threshold;
    always @(posedge clk) begin
        if (rst) begin
            filter_acc<=0; magnitude<=0; peak<=0; level<=0; candidate<=0;
            stable<=0; phase<=0; bit_data<=0; bit_valid<=0;
        end else begin
            bit_valid<=0;
            if (adc_valid) begin
                filter_acc<=filter_acc-{3'b0,filtered}+{3'b0,mag_sum[17:2]};
                magnitude<=filtered;
                if (filtered>peak) peak<=filtered;
                if (proposed==level) begin candidate<=level; stable<=0; end
                else if (candidate!=proposed) begin candidate<=proposed; stable<=1; end
                else if (stable==3) begin level<=proposed; stable<=0; end
                else stable<=stable+1'b1;
                if (proposed!=level && candidate==proposed && stable==3) phase<=0;
                else if (phase==SYMBOL_CLOCKS-1) phase<=0;
                else phase<=phase+1'b1;
                if (phase==SYMBOL_CLOCKS/2-1) begin bit_data<=level; bit_valid<=1; end
            end
        end
    end
endmodule
