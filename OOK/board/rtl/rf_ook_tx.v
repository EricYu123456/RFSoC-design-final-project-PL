`timescale 1ns/1ps
// RFDC supplies the RF carrier. This serializer supplies its OOK envelope.
module rf_ook_tx # (parameter integer PREAMBLE_BITS = 64) (
    input wire clk, input wire rst, input wire symbol_end,
    input wire [7:0] s_data, input wire s_valid, output wire s_ready,
    input wire s_last, output wire envelope, output wire busy,
    output reg frame_done, output reg underrun
);
    localparam IDLE=0, TRAIN=1, DATA=2;
    reg [1:0] state;
    reg [7:0] pending_data, shift_data;
    reg pending_valid, pending_last, current_last;
    reg [2:0] bit_index;
    reg [7:0] preamble_index;
    assign s_ready = !pending_valid && !rst;
    assign busy = state != IDLE || pending_valid;
    assign envelope = state == TRAIN ? !preamble_index[0] :
                      state == DATA ? shift_data[7] : 1'b0;
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; pending_data<=0; shift_data<=0; pending_valid<=0;
            pending_last<=0; current_last<=0; bit_index<=7; preamble_index<=0;
            frame_done<=0; underrun<=0;
        end else begin
            frame_done<=0;
            if (s_valid && s_ready) begin
                pending_data<=s_data; pending_last<=s_last; pending_valid<=1;
            end
            if (symbol_end) begin
                case (state)
                    IDLE: if (pending_valid) begin state<=TRAIN; preamble_index<=0; end
                    TRAIN: if (preamble_index == PREAMBLE_BITS-1) begin
                        shift_data<=pending_data; current_last<=pending_last;
                        pending_valid<=0; bit_index<=7; state<=DATA;
                    end else preamble_index<=preamble_index+1'b1;
                    DATA: if (bit_index != 0) begin
                        shift_data<={shift_data[6:0],1'b0}; bit_index<=bit_index-1'b1;
                    end else if (current_last) begin state<=IDLE; frame_done<=1; end
                    else if (pending_valid) begin
                        shift_data<=pending_data; current_last<=pending_last;
                        pending_valid<=0; bit_index<=7;
                    end else begin state<=IDLE; underrun<=1; end
                    default: state<=IDLE;
                endcase
            end
        end
    end
endmodule
