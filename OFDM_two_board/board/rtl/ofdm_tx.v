`timescale 1ns/1ps
// Four nonzero bins of an 8-point IFFT. Each data symbol carries one byte.
module ofdm_tx (
    input wire clk, input wire rst,
    input wire [7:0] s_data, input wire s_valid, output wire s_ready,
    input wire s_last, output wire busy,
    output reg signed [12:0] sample_i, output reg signed [12:0] sample_q,
    output reg frame_done, output reg underrun
);
    localparam IDLE=0, MARKER=1, TRAIN=2, DATA=3;
    localparam MARKER_SAMPLES=24;
    // Send a channel reference before each group of up to eight data symbols.
    localparam [2:0] LAST_DATA_IN_GROUP = 3'd7;
    reg [1:0] state;
    reg [2:0] sample_clock;
    reg [2:0] data_in_group;
    reg [4:0] symbol_position;
    reg [7:0] pending_data, current_data;
    reg pending_valid, pending_last, current_last;
    wire data_symbol_start = state == DATA && symbol_position == 0 && sample_clock == 0;
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
        input negate_i, negate_q, training;
        reg signed [9:0] from_i, from_q;
        begin
            from_i = cosine(angle);
            from_q = cosine(angle + 3'd2);
            if (training) component = from_i;
            else component = (negate_i ? -from_i : from_i) + (negate_q ? -from_q : from_q);
        end
    endfunction

    // Positions 0..3 are the cyclic prefix (samples 4..7). Then send samples 0..7.
    wire [2:0] phase_pos = symbol_position[2:0] + 3'd4;
    wire [2:0] phase_neg = -phase_pos;
    wire [2:0] phase_double = {phase_pos[1:0], 1'b0};
    wire [2:0] phase_neg_double = -phase_double;
    wire training = state == TRAIN;
    wire signed [12:0] ifft_i =
        (component(phase_pos, current_data[7], current_data[6], training)
       + component(phase_double, current_data[5], current_data[4], training))
       + (component(phase_neg_double, current_data[3], current_data[2], training)
       + component(phase_neg, current_data[1], current_data[0], training));
    // Real component at theta-pi/2 is the imaginary component at theta.
    wire signed [12:0] ifft_q =
        (component(phase_pos - 3'd2, current_data[7], current_data[6], training)
       + component(phase_double - 3'd2, current_data[5], current_data[4], training))
       + (component(phase_neg_double - 3'd2, current_data[3], current_data[2], training)
       + component(phase_neg - 3'd2, current_data[1], current_data[0], training));

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            sample_clock <= 0;
            data_in_group <= 0;
            symbol_position <= 0;
            pending_valid <= 0;
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
            sample_i <= state == MARKER ? 13'sd2048 : (state == IDLE ? 13'sd0 : ifft_i);
            sample_q <= (state == TRAIN || state == DATA) ? ifft_q : 13'sd0;
            if (s_valid && s_ready) begin
                pending_data <= s_data;
                pending_last <= s_last;
                pending_valid <= 1;
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
                        if (state == DATA) data_in_group <= data_in_group + 1'b1;
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
