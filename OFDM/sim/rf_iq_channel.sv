`timescale 1ns/1ps

// Digital cable model at the RFDC stream interface. One beat contains two
// consecutive complex samples, with the oldest I/Q pair in the low half.
module rf_iq_channel #(
    parameter integer DELAY_CLOCKS = 37,
    parameter integer HALF_BEAT_DELAY = 0,
    parameter integer GAIN_I = -5,
    parameter integer GAIN_Q = 3,
    parameter integer ECHO_GAIN = 0,
    parameter integer ECHO_CLOCKS = 8
) (
    input wire clk,
    input wire [63:0] dac_samples,
    output wire [31:0] adc_i, adc_q
);
    localparam integer DEPTH = DELAY_CLOCKS + ECHO_CLOCKS;
    localparam integer INDEX_BITS = $clog2(DEPTH);
    localparam [INDEX_BITS-1:0] LAST_INDEX = DEPTH - 1;
    localparam [INDEX_BITS-1:0] DIRECT_OFFSET = ECHO_CLOCKS;
    localparam [INDEX_BITS-1:0] DIRECT_WRAP = DELAY_CLOCKS;
    reg [63:0] cable [0:DEPTH-1];
    reg [INDEX_BITS-1:0] head = 0;
    reg [31:0] previous_late_sample = 0;
    reg [15:0] noise = 16'hace1;
    reg [1:0] noise_clock = 0;
    wire [INDEX_BITS-1:0] direct_index =
        (head < DIRECT_WRAP) ? head + DIRECT_OFFSET : head - DIRECT_WRAP;
    wire [63:0] direct = cable[direct_index];
    wire [63:0] echo = (ECHO_GAIN == 0) ? 64'd0 : cable[head];
    integer index;

    initial begin
        for (index = 0; index < DEPTH; index = index + 1)
            cable[index] = 0;
    end

    always @(posedge clk) begin
        cable[head] <= HALF_BEAT_DELAY ? {dac_samples[31:0], previous_late_sample} : dac_samples;
        previous_late_sample <= dac_samples[63:32];
        head <= head == LAST_INDEX ? 0 : head + 1'b1;
        // Hold the deterministic noise for four beats to model some bandwidth limit.
        noise_clock <= noise_clock + 1'b1;
        if (noise_clock == 0)
            noise <= {noise[14:0], noise[15] ^ noise[13] ^ noise[12] ^ noise[10]};
    end

    function automatic [31:0] mix(
        input logic signed [15:0] i, q, echo_i, echo_q,
        input logic [5:0] noise_bits
    );
        integer i_sum, q_sum;
        reg [15:0] i_out, q_out;
        begin
            // Complex gain (GAIN_I + j*GAIN_Q)/16 and real echo ECHO_GAIN/16.
            i_sum = GAIN_I * i - GAIN_Q * q + ECHO_GAIN * echo_i;
            q_sum = GAIN_Q * i + GAIN_I * q + ECHO_GAIN * echo_q;
            i_out = (i_sum >>> 4) + $unsigned(noise_bits[2:0]) - 3;
            q_out = (q_sum >>> 4) + $unsigned(noise_bits[5:3]) - 3;
            mix = {q_out, i_out};
        end
    endfunction

    wire [31:0] early = mix(direct[15:0], direct[31:16], echo[15:0], echo[31:16], noise[5:0]);
    wire [31:0] late = mix(direct[47:32], direct[63:48], echo[47:32], echo[63:48], noise[11:6]);
    assign adc_i = {late[15:0], early[15:0]};
    assign adc_q = {late[31:16], early[31:16]};
endmodule
