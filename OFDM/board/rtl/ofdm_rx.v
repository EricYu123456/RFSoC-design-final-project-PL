`timescale 1ns/1ps
// Acquire each burst from the ADC, then recover four bins of an 8-point DFT.
module ofdm_rx (
    input wire clk, input wire rst,
    input wire [31:0] adc_i, input wire [31:0] adc_q, input wire adc_valid,
    input wire [15:0] threshold,
    output reg frame_start, output reg is_ack,
    output reg [7:0] byte_data, output reg byte_valid,
    output wire [15:0] magnitude, output reg [15:0] peak
);
    function [15:0] abs16;
        input [15:0] x;
        begin abs16 = x[15] ? -x : x; end
    endfunction

    reg [15:0] abs_i0, abs_i1, abs_q0, abs_q1, raw_magnitude;
    wire [17:0] magnitude_sum = {2'b0,abs_i0} + {2'b0,abs_i1}
                              + {2'b0,abs_q0} + {2'b0,abs_q1};
    reg [18:0] filter_acc;
    assign magnitude = filter_acc[18:3];
    always @(posedge clk) begin
        if (rst) begin
            abs_i0 <= 0; abs_i1 <= 0; abs_q0 <= 0; abs_q1 <= 0;
            raw_magnitude <= 0;
            filter_acc <= 0;
            peak <= 0;
        end else if (adc_valid) begin
            abs_i0 <= abs16(adc_i[15:0]);
            abs_i1 <= abs16(adc_i[31:16]);
            abs_q0 <= abs16(adc_q[15:0]);
            abs_q1 <= abs16(adc_q[31:16]);
            raw_magnitude <= magnitude_sum[17] ? 16'hffff : magnitude_sum[16:1];
            filter_acc <= filter_acc - {3'b0,magnitude} + {3'b0,raw_magnitude};
            if (magnitude > peak) peak <= magnitude;
        end
    end

    reg receiving, armed, first_byte;
    reg [1:0] marker_cycles;
    reg [7:0] quiet_cycles, clocks_to_sample;
    reg [2:0] sample_index;
    reg [10:0] remaining_bytes;
    // The idle gate must allow the baseline measured during pilot calibration.
    // While locked, use a lower limit because OFDM samples have varying amplitude.
    wire [15:0] signal_floor = threshold >> 2;
    wire [15:0] idle_limit = threshold - signal_floor;
    wire quiet = raw_magnitude <= (receiving ? signal_floor : idle_limit);
    wire capture = receiving && adc_valid && clocks_to_sample == 0;
    wire [7:0] decisions;
    reg product_valid;

    always @(posedge clk) begin
        if (rst || !adc_valid) begin
            receiving <= 0;
            armed <= 0;
            marker_cycles <= 0;
            quiet_cycles <= 0;
            clocks_to_sample <= 0;
            sample_index <= 0;
            first_byte <= 1;
            remaining_bytes <= 0;
            is_ack <= 0;
            byte_data <= 0;
            byte_valid <= 0;
            frame_start <= 0;
        end else begin
            byte_valid <= 0;
            frame_start <= 0;
            if (!quiet) quiet_cycles <= 0;
            else if (quiet_cycles < 128) quiet_cycles <= quiet_cycles + 1'b1;
            if (!receiving) begin
                if (quiet_cycles >= 32) armed <= 1;
                if (armed && raw_magnitude > threshold) begin
                    marker_cycles <= marker_cycles + 1'b1;
                    if (marker_cycles == 2) begin
                        receiving <= 1;
                        frame_start <= 1;
                        armed <= 0;
                        marker_cycles <= 0;
                        first_byte <= 1;
                        sample_index <= 0;
                        // Skip the 24-sample marker and three samples of the CP.
                        // The window begins one sample early to allow for delayed
                        // threshold crossings. Training absorbs cyclic rotation.
                        clocks_to_sample <= (24+3)*8-1;
                    end
                end else marker_cycles <= 0;
            end else if (quiet_cycles == 128) begin
                receiving <= 0;
            end else begin
                if (capture) begin
                    sample_index <= sample_index + 1'b1;
                    clocks_to_sample <= sample_index == 7 ? 39 : 7;
                end else clocks_to_sample <= clocks_to_sample - 1'b1;
                if (product_valid) begin
                    byte_data <= decisions;
                    if (first_byte) begin
                        first_byte <= 0;
                        is_ack <= decisions == 8'hac;
                        case (decisions)
                            8'hd3: begin remaining_bytes <= 1037; byte_valid <= 1; end
                            8'hac: begin remaining_bytes <= 11; byte_valid <= 1; end
                            default: receiving <= 0;
                        endcase
                    end else begin
                        byte_valid <= 1;
                        remaining_bytes <= remaining_bytes - 1'b1;
                        if (remaining_bytes == 1) receiving <= 0;
                    end
                end
            end
        end
    end

    // Rotate by exp(-j*phase*pi/4). The real part of a rotated (Q,-I)
    // is the imaginary part of the same rotation applied to (I,Q).
    function signed [16:0] rotated_real;
        input signed [16:0] i, q, i45, q45;
        input [2:0] phase;
        begin
            case (phase)
                0: rotated_real = i;
                1: rotated_real = i45 + q45;
                2: rotated_real = q;
                3: rotated_real = q45 - i45;
                4: rotated_real = -i;
                5: rotated_real = -i45 - q45;
                6: rotated_real = -q;
                7: rotated_real = i45 - q45;
            endcase
        end
    endfunction

    reg captured, weighted, rotated, transform_valid, trained;
    reg [2:0] captured_index, weighted_index, rotated_index;
    reg signed [15:0] captured_i, captured_q;
    reg signed [16:0] weighted_i, weighted_q;
    reg signed [24:0] weighted_i_product, weighted_q_product;
    wire signed [16:0] i45 = weighted_i_product >>> 8;
    wire signed [16:0] q45 = weighted_q_product >>> 8;
    always @(posedge clk) begin
        if (rst || !receiving || !adc_valid) begin
            captured <= 0;
            weighted <= 0;
            rotated <= 0;
            transform_valid <= 0;
            trained <= 0;
            product_valid <= 0;
        end else begin
            captured <= capture;
            weighted <= captured;
            rotated <= weighted;
            transform_valid <= rotated && rotated_index == 7;
            product_valid <= transform_valid && trained;
            if (transform_valid) trained <= 1;
        end
        if (capture) begin
            captured_index <= sample_index;
            captured_i <= adc_i[15:0];
            captured_q <= adc_q[15:0];
        end
        if (captured) begin
            weighted_index <= captured_index;
            weighted_i <= captured_i;
            weighted_q <= captured_q;
            weighted_i_product <= captured_i * 9'sd181;
            weighted_q_product <= captured_q * 9'sd181;
        end
        if (weighted) rotated_index <= weighted_index;
    end

    genvar tone;
    generate for (tone=0; tone<4; tone=tone+1) begin : bin_dft
        localparam [2:0] BIN = tone < 2 ? tone+1 : tone+4;
        wire [2:0] phase = weighted_index * BIN;
        reg signed [16:0] rotated_i, rotated_q;
        reg signed [19:0] sum_i, sum_q;
        wire signed [16:0] bin_i = sum_i[19:3];
        wire signed [16:0] bin_q = sum_q[19:3];
        reg signed [16:0] channel_i, channel_q;
        reg signed [33:0] ii, qq, qi, iq;
        wire signed [34:0] equalized_i = ii + qq;
        wire signed [34:0] equalized_q = qi - iq;
        assign decisions[7-2*tone] = equalized_i[34];
        assign decisions[6-2*tone] = equalized_q[34];

        always @(posedge clk) begin
            if (weighted) begin
                rotated_i <= rotated_real(weighted_i,weighted_q,i45,q45,phase);
                rotated_q <= rotated_real(weighted_q,-weighted_i,q45,-i45,phase);
            end
            if (rotated) begin
                sum_i <= rotated_index == 0 ? rotated_i : sum_i + rotated_i;
                sum_q <= rotated_index == 0 ? rotated_q : sum_q + rotated_q;
            end
            if (transform_valid) begin
                if (!trained) begin
                    // Each active training bin transmits +1+0j.
                    channel_i <= bin_i;
                    channel_q <= bin_q;
                end else begin
                    // Multiply by conjugate H. Positive |H|^2 scaling preserves signs.
                    ii <= bin_i * channel_i;
                    qq <= bin_q * channel_q;
                    qi <= bin_q * channel_i;
                    iq <= bin_i * channel_q;
                end
            end
        end
    end endgenerate
endmodule
