`timescale 1ns/1ps
// Finds sync in the received bits. No TX packet-boundary sideband is used.
module packet_rx #(
    parameter [31:0] SYNC_WORD = 32'hD391C5A7,
    parameter integer HEADER_BYTES = 6,
    parameter integer PAYLOAD_BYTES = 1024
) (
    input wire clk, input wire rst, input wire bit_data, input wire bit_valid,
    output reg packet_done, output reg crc_ok,
    output reg [15:0] frame_id, output reg [15:0] packet_id,
    output reg [15:0] payload_len,
    output reg payload_we, output reg [9:0] payload_addr,
    output reg [7:0] payload_data
);
    `include "crc32_functions.vh"
    localparam integer CONTENT_BYTES = HEADER_BYTES + PAYLOAD_BYTES;
    reg receiving;
    reg [31:0] sync_shift, crc_state, received_crc;
    reg [7:0] byte_shift;
    reg [2:0] bit_count;
    reg [10:0] byte_index;
    wire [7:0] next_byte = {byte_shift[6:0], bit_data};
    always @(posedge clk) begin
        if (rst) begin
            receiving <= 0; sync_shift <= 0; crc_state <= 32'hFFFFFFFF;
            received_crc <= 0; byte_shift <= 0; bit_count <= 0; byte_index <= 0;
            frame_id <= 0; packet_id <= 0; payload_len <= 0;
            packet_done <= 0; crc_ok <= 0; payload_we <= 0;
            payload_addr <= 0; payload_data <= 0;
        end else begin
            packet_done <= 0; payload_we <= 0;
            if (bit_valid) begin
                sync_shift <= {sync_shift[30:0], bit_data};
                if (!receiving) begin
                    if ({sync_shift[30:0], bit_data} == SYNC_WORD) begin
                        receiving <= 1; byte_index <= 0; bit_count <= 0;
                        byte_shift <= 0; received_crc <= 0; crc_state <= 32'hFFFFFFFF;
                        frame_id <= 0; packet_id <= 0; payload_len <= 0;
                    end
                end else begin
                    byte_shift <= next_byte;
                    if (bit_count != 7) bit_count <= bit_count + 1'b1;
                    else begin
                        bit_count <= 0; byte_index <= byte_index + 1'b1;
                        if (byte_index < CONTENT_BYTES) begin
                            crc_state <= crc32_update(crc_state, next_byte);
                            case (byte_index)
                                0: frame_id[15:8] <= next_byte;
                                1: frame_id[7:0] <= next_byte;
                                2: packet_id[15:8] <= next_byte;
                                3: packet_id[7:0] <= next_byte;
                                default: begin end
                            endcase
                            if (HEADER_BYTES == 6) begin
                                if (byte_index == 4) payload_len[15:8] <= next_byte;
                                if (byte_index == 5) payload_len[7:0] <= next_byte;
                            end
                            if (byte_index >= HEADER_BYTES) begin
                                payload_we <= 1; payload_addr <= byte_index - HEADER_BYTES;
                                payload_data <= next_byte;
                            end
                        end else begin
                            received_crc <= {received_crc[23:0], next_byte};
                            if (byte_index == CONTENT_BYTES + 3) begin
                                packet_done <= 1;
                                crc_ok <= ({received_crc[23:0], next_byte} == ~crc_state);
                                receiving <= 0; sync_shift <= 0;
                            end
                        end
                    end
                end
            end
        end
    end
endmodule
