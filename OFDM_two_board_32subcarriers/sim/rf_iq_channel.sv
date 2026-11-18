`timescale 1ns/1fs

// Separate transmit and receive fabric clocks approximate an analog cable:
// sample the delayed, held DAC value on the local ADC clock. The complex
// gain, echo, noise, and rotating phasor are applied after downconversion.
module rf_iq_channel #(
    parameter integer DELAY_CLOCKS = 37,
    parameter integer HALF_BEAT_DELAY = 0,
    parameter integer GAIN_I = -5,
    parameter integer GAIN_Q = 3,
    parameter integer ECHO_GAIN = 0,
    parameter integer ECHO_CLOCKS = 8,
    parameter integer CFO_HZ = 0
) (
    input wire tx_clk, rx_clk,
    input wire [63:0] dac_samples,
    output wire [31:0] adc_i, adc_q
);
    localparam integer DEPTH = DELAY_CLOCKS + ECHO_CLOCKS;
    localparam integer INDEX_BITS = $clog2(DEPTH);
    localparam [INDEX_BITS-1:0] LAST_INDEX = DEPTH - 1;
    localparam [INDEX_BITS-1:0] DIRECT_OFFSET = ECHO_CLOCKS;
    localparam [INDEX_BITS-1:0] DIRECT_WRAP = DELAY_CLOCKS;
    localparam [31:0] PHASE_STEP = $rtoi(4294967296.0 * CFO_HZ / 153600000.0);
    reg [63:0] cable [0:DEPTH-1];
    reg [INDEX_BITS-1:0] head = 0;
    reg [31:0] previous_late_sample = 0;
    reg [63:0] direct_rx = 0, echo_rx = 0;
    reg [15:0] noise = 16'hace1;
    reg [1:0] noise_clock = 0;
    reg [31:0] phase = 0;
    reg signed [11:0] cosine [0:1023];
    reg signed [11:0] sine [0:1023];
    wire [INDEX_BITS-1:0] direct_index =
        (head < DIRECT_WRAP) ? head + DIRECT_OFFSET : head - DIRECT_WRAP;

    integer index;
    real angle;
    initial begin
        for (index = 0; index < DEPTH; index = index + 1)
            cable[index] = 0;
        for (index = 0; index < 1024; index = index + 1) begin
            angle = 6.283185307179586 * index / 1024.0;
            cosine[index] = $rtoi(1024.0 * $cos(angle));
            sine[index] = $rtoi(1024.0 * $sin(angle));
        end
    end

    always @(posedge tx_clk) begin
        cable[head] <= HALF_BEAT_DELAY ? {dac_samples[31:0], previous_late_sample} : dac_samples;
        previous_late_sample <= dac_samples[63:32];
        head <= head == LAST_INDEX ? 0 : head + 1'b1;
    end

    always @(posedge rx_clk) begin
        direct_rx <= cable[direct_index];
        echo_rx <= ECHO_GAIN == 0 ? 64'd0 : cable[head];
        phase <= phase + PHASE_STEP;
        // Hold the deterministic noise for four beats to approximate bandwidth limits.
        noise_clock <= noise_clock + 1'b1;
        if (noise_clock == 0)
            noise <= {noise[14:0], noise[15] ^ noise[13] ^ noise[12] ^ noise[10]};
    end

    function automatic [31:0] mix(
        input logic signed [15:0] i, q, echo_i, echo_q,
        input logic signed [11:0] c, s,
        input logic [5:0] noise_bits
    );
        integer i_sum, q_sum, i_rotated, q_rotated;
        reg [15:0] i_out, q_out;
        begin
            i_sum = GAIN_I * i - GAIN_Q * q + ECHO_GAIN * echo_i;
            q_sum = GAIN_Q * i + GAIN_I * q + ECHO_GAIN * echo_q;
            i_rotated = (i_sum * c - q_sum * s) >>> 14;
            q_rotated = (q_sum * c + i_sum * s) >>> 14;
            i_out = i_rotated + $unsigned(noise_bits[2:0]) - 3;
            q_out = q_rotated + $unsigned(noise_bits[5:3]) - 3;
            mix = {q_out, i_out};
        end
    endfunction

    wire signed [11:0] c = CFO_HZ == 0 ? 12'sd1024 : cosine[phase[31:22]];
    wire signed [11:0] s = CFO_HZ == 0 ? 12'sd0 : sine[phase[31:22]];
    wire [31:0] early = mix(direct_rx[15:0], direct_rx[31:16], echo_rx[15:0], echo_rx[31:16],
                            c, s, noise[5:0]);
    wire [31:0] late = mix(direct_rx[47:32], direct_rx[63:48], echo_rx[47:32], echo_rx[63:48],
                           c, s, noise[11:6]);
    assign adc_i = {late[15:0], early[15:0]};
    assign adc_q = {late[31:16], early[31:16]};
endmodule
