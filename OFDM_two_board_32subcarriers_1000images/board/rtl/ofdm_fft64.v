`timescale 1ns/1ps
// 64-point DIT radix-2 transform. Natural order in/out, with 1/2 scaling
// per stage and Q14 twiddles. The IFFT uses the positive sine.
module ofdm_fft64 #(
    parameter INVERSE = 0
) (
    input wire clk, input wire rst, input wire start,
    input wire signed [15:0] in_i, in_q, input wire in_valid,
    output wire in_ready,
    output wire signed [16:0] out_i, out_q,
    output reg out_valid, output reg [5:0] out_index
);
    localparam IDLE=0, LOAD=1, COMPUTE=2, DRAIN=3, UNLOAD=4;
    reg [2:0] state;
    reg [5:0] index, stage_bit;
    reg [2:0] stage;
    wire [5:0] mask = stage_bit - 1'b1;
    wire [5:0] offset = index & mask;
    wire [5:0] a_addr = ((index & ~mask) << 1) | offset;
    wire [5:0] b_addr = a_addr | stage_bit;
    wire [4:0] phase = offset[4:0] << (3'd5 - stage);
    wire [5:0] reverse = {index[0],index[1],index[2],index[3],index[4],index[5]};
    assign in_ready = state == LOAD;

    function [31:0] twiddle;
        input [4:0] angle;
        begin
            case (angle)
                5'd0: twiddle = {16'sd16384, 16'sd0};
                5'd1: twiddle = {16'sd16305, 16'sd1606};
                5'd2: twiddle = {16'sd16069, 16'sd3196};
                5'd3: twiddle = {16'sd15679, 16'sd4756};
                5'd4: twiddle = {16'sd15137, 16'sd6270};
                5'd5: twiddle = {16'sd14449, 16'sd7723};
                5'd6: twiddle = {16'sd13623, 16'sd9102};
                5'd7: twiddle = {16'sd12665, 16'sd10394};
                5'd8: twiddle = {16'sd11585, 16'sd11585};
                5'd9: twiddle = {16'sd10394, 16'sd12665};
                5'd10: twiddle = {16'sd9102, 16'sd13623};
                5'd11: twiddle = {16'sd7723, 16'sd14449};
                5'd12: twiddle = {16'sd6270, 16'sd15137};
                5'd13: twiddle = {16'sd4756, 16'sd15679};
                5'd14: twiddle = {16'sd3196, 16'sd16069};
                5'd15: twiddle = {16'sd1606, 16'sd16305};
                5'd16: twiddle = {16'sd0, 16'sd16384};
                5'd17: twiddle = {-16'sd1606, 16'sd16305};
                5'd18: twiddle = {-16'sd3196, 16'sd16069};
                5'd19: twiddle = {-16'sd4756, 16'sd15679};
                5'd20: twiddle = {-16'sd6270, 16'sd15137};
                5'd21: twiddle = {-16'sd7723, 16'sd14449};
                5'd22: twiddle = {-16'sd9102, 16'sd13623};
                5'd23: twiddle = {-16'sd10394, 16'sd12665};
                5'd24: twiddle = {-16'sd11585, 16'sd11585};
                5'd25: twiddle = {-16'sd12665, 16'sd10394};
                5'd26: twiddle = {-16'sd13623, 16'sd9102};
                5'd27: twiddle = {-16'sd14449, 16'sd7723};
                5'd28: twiddle = {-16'sd15137, 16'sd6270};
                5'd29: twiddle = {-16'sd15679, 16'sd4756};
                5'd30: twiddle = {-16'sd16069, 16'sd3196};
                5'd31: twiddle = {-16'sd16305, 16'sd1606};
            endcase
        end
    endfunction

    // Bank by address parity: the two addresses of every butterfly differ
    // in one bit, so each bank needs only one read and one write per clock.
    reg [33:0] even_bank [0:31];
    reg [33:0] odd_bank [0:31];
    wire a_odd = ^a_addr;
    reg [33:0] even_read, odd_read;
    reg v0, v1, v2, a_odd0, a_odd1, a_odd2;
    reg [4:0] a0, b0, a1, b1, a2, b2;
    reg signed [15:0] wr, wi;
    wire [31:0] coefficient = twiddle(phase);
    wire [33:0] a_word = a_odd0 ? odd_read : even_read;
    wire [33:0] b_word = a_odd0 ? even_read : odd_read;
    wire signed [16:0] br = b_word[33:17];
    wire signed [16:0] bi = b_word[16:0];
    reg signed [16:0] ar1, ai1, ar2, ai2;
    reg signed [32:0] brwr, biwi, brwi, biwr;
    wire signed [33:0] real_product = brwr - biwi;
    wire signed [33:0] imag_product = brwi + biwr;
    reg signed [17:0] tr, ti;
    wire signed [18:0] sum_i = ar2 + tr, sum_q = ai2 + ti;
    wire signed [18:0] diff_i = ar2 - tr, diff_q = ai2 - ti;
    wire [33:0] result_a = {sum_i[17:1], sum_q[17:1]};
    wire [33:0] result_b = {diff_i[17:1], diff_q[17:1]};
    wire [33:0] input_word = {in_i[15],in_i,in_q[15],in_q};
    wire load = in_ready && in_valid && !rst;
    wire output_odd = ^out_index;
    assign {out_i,out_q} = output_odd ? odd_read : even_read;

    always @(posedge clk) begin
        if (state == COMPUTE) begin
            even_read <= even_bank[a_odd ? b_addr[4:0] : a_addr[4:0]];
            odd_read <= odd_bank[a_odd ? a_addr[4:0] : b_addr[4:0]];
            a_odd0 <= a_odd;
            a0 <= a_addr[4:0]; b0 <= b_addr[4:0];
            wr <= coefficient[31:16];
            wi <= INVERSE ? coefficient[15:0] : -coefficient[15:0];
        end else if (state == UNLOAD) begin
            even_read <= even_bank[index[4:0]];
            odd_read <= odd_bank[index[4:0]];
        end
        if (v0) begin
            ar1 <= a_word[33:17]; ai1 <= a_word[16:0];
            a_odd1 <= a_odd0; a1 <= a0; b1 <= b0;
            brwr <= br * wr;
            biwi <= bi * wi;
            brwi <= br * wi;
            biwr <= bi * wr;
        end
        if (v1) begin
            ar2 <= ar1; ai2 <= ai1;
            a_odd2 <= a_odd1; a2 <= a1; b2 <= b1;
            tr <= real_product >>> 14;
            ti <= imag_product >>> 14;
        end
        if (!rst && v2) begin
            even_bank[a_odd2 ? b2 : a2] <= a_odd2 ? result_b : result_a;
            odd_bank[a_odd2 ? a2 : b2] <= a_odd2 ? result_a : result_b;
        end else if (load) begin
            if (^reverse) odd_bank[reverse[4:0]] <= input_word;
            else even_bank[reverse[4:0]] <= input_word;
        end

        if (rst) begin
            state <= IDLE;
            index <= 0; stage <= 0; stage_bit <= 1;
            v0 <= 0; v1 <= 0; v2 <= 0;
            out_valid <= 0; out_index <= 0;
        end else begin
            v0 <= state == COMPUTE; v1 <= v0; v2 <= v1;
            out_valid <= state == UNLOAD;
            case (state)
                IDLE: if (start) begin state <= LOAD; index <= 0; end
                LOAD: if (in_valid) begin
                    index <= index + 1'b1;
                    if (index == 63) begin state <= COMPUTE; stage <= 0; stage_bit <= 1; end
                end
                COMPUTE: begin
                    index <= index + 1'b1;
                    if (index == 31) state <= DRAIN;
                end
                DRAIN: if (!(v0 || v1 || v2)) begin
                    index <= 0;
                    if (stage == 5) state <= UNLOAD;
                    else begin
                        stage <= stage + 1'b1;
                        stage_bit <= stage_bit << 1;
                        state <= COMPUTE;
                    end
                end
                UNLOAD: begin
                    out_index <= index;
                    index <= index + 1'b1;
                    if (index == 63) state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
