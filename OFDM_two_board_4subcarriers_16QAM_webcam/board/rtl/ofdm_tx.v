`timescale 1ns/1ps
// Four nonzero bins of an 8-point IFFT. Four Gray-coded 16-QAM tones carry two bytes per symbol, high byte first.
module ofdm_tx (
    input wire clk, input wire rst,
    input wire [7:0] s_data, input wire s_valid, output wire s_ready,
    input wire s_last, output wire busy,
    output reg signed [12:0] sample_i, output reg signed [12:0] sample_q,
    output reg frame_done, output reg underrun
);
    localparam IDLE=0, MARKER=1, TRAIN=2, DATA=3;
    localparam MARKER_SAMPLES=24;
    // Send a channel reference before each group of four data symbols.
    localparam [2:0] LAST_DATA_IN_GROUP = 3'd3;
    reg [1:0] state;
    reg [2:0] sample_clock;
    reg [2:0] data_in_group;
    reg [4:0] symbol_position;
    reg [15:0] pending_data, current_data;
    reg half_full;
    reg pending_valid, pending_last, current_last;
    assign s_ready = !pending_valid && !rst;
    assign busy = state != IDLE || pending_valid;

    function signed [9:0] cosine;
        input [2:0] angle;
        begin
            case (angle)
                0: cosine = 256;
                1,7: cosine = 181;
                2,6: cosine = 0;
                3,5: cosine = -181;
                4: cosine = -256;
            endcase
        end
    endfunction

    function signed [12:0] component;
        input [2:0] angle;
        input [3:0] bits;
        input training;
        reg signed [12:0] c, s, i_part, q_part;
        begin
            c = cosine(angle);
            s = cosine(angle + 3'd2);
            // 00/01/11/10 map to +3/+1/-1/-3 on each axis.
            i_part = bits[2] ? c : c + (c <<< 1);
            q_part = bits[0] ? s : s + (s <<< 1);
            component = training ? (c <<< 1) :
                (bits[3] ? -i_part : i_part) + (bits[1] ? -q_part : q_part);
        end
    endfunction

    // Positions 0..3 are the cyclic prefix (samples 4..7). Then send samples 0..7.
    wire [2:0] phase_pos = symbol_position[2:0] + 3'd4;
    wire [2:0] phase_neg = -phase_pos;
    wire [2:0] phase_double = {phase_pos[1:0], 1'b0};
    wire [2:0] phase_neg_double = -phase_double;
    wire training = state == TRAIN;
    // Register each tone before summing. Delay the marker and enable by
    // the same stage so the receiver sees the original CP/marker boundary.
    wire [2:0] tone_phase [0:3];
    assign tone_phase[0] = phase_pos;
    assign tone_phase[1] = phase_double;
    assign tone_phase[2] = phase_neg_double;
    assign tone_phase[3] = phase_neg;
    reg signed [12:0] tone_i [0:3], tone_q [0:3];
    genvar tone;
    generate for (tone=0; tone<4; tone=tone+1) begin : mapper
        wire [3:0] bits = current_data[15-4*tone -: 4];
        always @(posedge clk) begin
            tone_i[tone] <= component(tone_phase[tone], bits, training);
            tone_q[tone] <= component(tone_phase[tone] - 3'd2, bits, training);
        end
    end endgenerate
    reg [1:0] output_state;
    always @(posedge clk) output_state <= rst ? IDLE : state;
    wire signed [12:0] ifft_i = (tone_i[0]+tone_i[1]) + (tone_i[2]+tone_i[3]);
    wire signed [12:0] ifft_q = (tone_q[0]+tone_q[1]) + (tone_q[2]+tone_q[3]);

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            sample_clock <= 0;
            data_in_group <= 0;
            symbol_position <= 0;
            pending_valid <= 0;
            half_full <= 0;
            pending_data <= 0;
            pending_last <= 0;
            current_data <= 0;
            current_last <= 0;
            frame_done <= 0;
            underrun <= 0;
            sample_i <= 0;
            sample_q <= 0;
        end else begin
            frame_done <= 0;
            sample_i <= output_state == MARKER ? 13'sd2048 : (output_state == IDLE ? 13'sd0 : ifft_i);
            sample_q <= (output_state == TRAIN || output_state == DATA) ? ifft_q : 13'sd0;
            if (s_valid && s_ready) begin
                if (half_full) pending_data[7:0] <= s_data;
                else pending_data <= {s_data, 8'h00};
                pending_last <= s_last;
                pending_valid <= half_full || s_last;
                half_full <= !half_full && !s_last;
            end
            if (state == IDLE) begin
                data_in_group <= 0;
                if (pending_valid) state <= MARKER;
            end else begin
                sample_clock <= sample_clock + 1'b1;
                if (sample_clock == 7) begin
                    symbol_position <= symbol_position + 1'b1;
                    if (state == MARKER && symbol_position == MARKER_SAMPLES-1) begin
                        state <= TRAIN;
                        symbol_position <= 0;
                    end else if ((state == TRAIN || state == DATA) && symbol_position == 11) begin
                        symbol_position <= 0;
                        if (state == DATA) data_in_group <= data_in_group == LAST_DATA_IN_GROUP ? 0 : data_in_group + 1'b1;
                        if (state == DATA && current_last) begin
                            state <= IDLE;
                            frame_done <= 1;
                        end else if (state == DATA && data_in_group == LAST_DATA_IN_GROUP) begin
                            state <= TRAIN;
                        end else if (pending_valid) begin
                            state <= DATA;
                            current_data <= pending_data;
                            current_last <= pending_last;
                            pending_valid <= 0;
                        end else begin
                            state <= IDLE;
                            underrun <= 1;
                        end
                    end
                end
            end
        end
    end
endmodule
