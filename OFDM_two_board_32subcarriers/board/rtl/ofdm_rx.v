`timescale 1ns/1ps
// Acquire each burst from the ADC, then recover 32 active bins of a 64-point FFT.
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

    `include "ofdm_phy.vh"
    localparam integer MARKER_CONFIRM = 16;
    localparam integer QUIET_ABORT = 2 * OFDM_SYMBOL_CLOCKS;
    // First capture is centered on the last CP sample. The training symbol
    // removes this cyclic rotation from the following data symbols.
    localparam integer FIRST_DELAY =
        (OFDM_MARKER + OFDM_CP - 1) * OFDM_SAMPLE_CLOCKS + OFDM_SAMPLE_CLOCKS/2
        - (MARKER_CONFIRM + 1) - 1;
    reg receiving, armed, first_byte;
    reg [3:0] marker_cycles;
    reg [10:0] quiet_cycles, remaining_bytes;
    reg [9:0] clocks_to_sample;
    reg [5:0] sample_index;
    wire [15:0] signal_floor = threshold >> 2;
    wire [15:0] idle_limit = threshold - signal_floor;
    wire quiet = raw_magnitude <= (receiving ? signal_floor : idle_limit);
    wire capture = receiving && adc_valid && clocks_to_sample == 0;
    wire decode_reset = rst || !adc_valid || !receiving;

    reg [31:0] captured [0:63];
    reg [31:0] fft_input;
    reg [6:0] load_issued;
    reg load_valid;
    wire fft_in_ready, fft_out_valid;
    wire [5:0] fft_bin;
    wire signed [16:0] bin_i, bin_q;
    ofdm_fft64 fft (.clk(clk),.rst(decode_reset),.start(capture && sample_index == OFDM_SAMPLES-1),
        .in_i(fft_input[31:16]),.in_q(fft_input[15:0]),.in_valid(load_valid),.in_ready(fft_in_ready),
        .out_i(bin_i),.out_q(bin_q),.out_valid(fft_out_valid),.out_index(fft_bin));

    always @(posedge clk) begin
        if (capture) captured[sample_index] <= {adc_i[15:0],adc_q[15:0]};
        if (fft_in_ready && !load_issued[6]) fft_input <= captured[load_issued[5:0]];
        if (decode_reset || !fft_in_ready) begin
            load_issued <= 0;
            load_valid <= 0;
        end else begin
            load_valid <= !load_issued[6];
            if (!load_issued[6]) load_issued <= load_issued + 1'b1;
        end
    end

    reg [1:0] symbol_in_group;
    wire training = symbol_in_group == 0;
    wire [4:0] tone = tone_number(fft_bin);
    wire signed [16:0] h_i, h_q;
    reg [33:0] channel [0:31];
    assign {h_i,h_q} = channel[tone];
    wire signed [16:0] train_i = training_negative(tone) ? -bin_i : bin_i;
    wire signed [16:0] train_q = training_negative(tone) ? -bin_q : bin_q;
    reg signed [33:0] ii, qq, qi, iq;
    wire signed [34:0] equalized_i = ii + qq;
    wire signed [34:0] equalized_q = qi - iq;
    reg bin_valid, bin_last;
    reg [63:0] decisions;
    wire [63:0] next_decisions = {decisions[61:0],equalized_i[34],equalized_q[34]};

    always @(posedge clk) begin
        if (decode_reset) begin
            symbol_in_group <= 0;
            bin_valid <= 0;
            bin_last <= 0;
        end else begin
            bin_valid <= fft_out_valid && active_bin(fft_bin) && !training;
            bin_last <= fft_bin == OFDM_SAMPLES-1;
            if (fft_out_valid && fft_bin == OFDM_SAMPLES-1)
                symbol_in_group <= symbol_in_group == OFDM_DATA_PER_TRAIN ? 2'd0 : symbol_in_group + 1'b1;
            if (fft_out_valid && active_bin(fft_bin)) begin
                if (training) channel[tone] <= {train_i,train_q};
                else begin
                    // Multiply by conjugate H; only the signs are needed.
                    ii <= bin_i * h_i; qq <= bin_q * h_q;
                    qi <= bin_q * h_i; iq <= bin_i * h_q;
                end
            end
            if (bin_valid) decisions <= next_decisions;
        end
    end

    reg [63:0] byte_shift;
    reg [3:0] bytes_left, byte_delay;
    wire [7:0] next_byte = byte_shift[63:56];
    always @(posedge clk) begin
        if (rst || !adc_valid) begin
            receiving <= 0; armed <= 0; first_byte <= 1;
            marker_cycles <= 0; quiet_cycles <= 0; clocks_to_sample <= 0;
            sample_index <= 0; remaining_bytes <= 0;
            bytes_left <= 0; byte_delay <= 0; byte_shift <= 0;
            is_ack <= 0; byte_data <= 0; byte_valid <= 0; frame_start <= 0;
        end else begin
            byte_valid <= 0;
            frame_start <= 0;
            if (!quiet) quiet_cycles <= 0;
            else if (quiet_cycles < QUIET_ABORT) quiet_cycles <= quiet_cycles + 1'b1;
            if (!receiving) begin
                if (quiet_cycles >= 32) armed <= 1;
                if (armed && raw_magnitude > threshold) begin
                    marker_cycles <= marker_cycles + 1'b1;
                    if (marker_cycles == MARKER_CONFIRM-1) begin
                        receiving <= 1; frame_start <= 1; armed <= 0;
                        marker_cycles <= 0; first_byte <= 1;
                        sample_index <= 0; clocks_to_sample <= FIRST_DELAY;
                        bytes_left <= 0; byte_delay <= 0;
                    end
                end else marker_cycles <= 0;
            end else if (quiet_cycles == QUIET_ABORT) begin
                receiving <= 0;
            end else begin
                if (capture) begin
                    sample_index <= sample_index + 1'b1;
                    clocks_to_sample <= sample_index == OFDM_SAMPLES-1 ?
                        (OFDM_CP + 1) * OFDM_SAMPLE_CLOCKS - 1 : OFDM_SAMPLE_CLOCKS - 1;
                end else clocks_to_sample <= clocks_to_sample - 1'b1;

                // Leave at least eight clocks for the downstream bit serializer.
                if (bin_valid && bin_last) begin
                    byte_shift <= next_decisions;
                    bytes_left <= OFDM_BYTES;
                    byte_delay <= 0;
                end else if (byte_delay != 0) byte_delay <= byte_delay - 1'b1;
                else if (bytes_left != 0) begin
                    byte_data <= next_byte;
                    byte_shift <= {byte_shift[55:0],8'd0};
                    bytes_left <= bytes_left - 1'b1;
                    byte_delay <= 15;
                    if (first_byte) begin
                        first_byte <= 0;
                        is_ack <= next_byte == 8'hac;
                        case (next_byte)
                            8'hd3: begin remaining_bytes <= 1041; byte_valid <= 1; end
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
endmodule
