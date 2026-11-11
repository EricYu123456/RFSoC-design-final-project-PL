`timescale 1ns/1ps
module image_sender #(
    parameter integer ACK_TIMEOUT = 4096,
    parameter integer GUARD_CLOCKS = 64,
    parameter integer MAX_RETRIES = 10
) (
    input wire clk, input wire rst, input wire start,
    input wire [15:0] configured_frame_id, input wire [15:0] total_packets,
    input wire [7:0] image_data, input wire image_valid, output wire image_ready,
    output reg [7:0] tx_data, output wire tx_valid, input wire tx_ready,
    output wire tx_last, input wire tx_done,
    input wire ack_done, input wire ack_crc_ok,
    input wire [15:0] ack_frame_id, input wire [15:0] ack_packet_id,
    output reg done, output reg failed,
    output reg [15:0] packet_id, output reg [15:0] retry_count,
    output reg [31:0] retransmissions, output reg [31:0] timeouts,
    output reg [31:0] ack_crc_errors, output reg [31:0] ignored_acks,
    output reg [15:0] acknowledged_packets, output reg [15:0] exhausted_packets,
    output reg [10:0] byte_index, output reg [3:0] state
);
    `include "crc32_functions.vh"
    localparam IDLE=0, HEADER=1, LOAD=2, SEND=3, WAIT_END=4,
               WAIT_ACK=5, GUARD=6, FINISHED=7, FAILURE=8;
    localparam [10:0] PAYLOAD_START = 11'd14, PACKET_CRC_START = 11'd1038;
    reg [15:0] frame_id, packets;
    reg [2:0] header_index;
    reg [9:0] load_index;
    reg [31:0] crc_state, header_crc, packet_crc;
    reg [31:0] timer;
    reg [1:0] settle;
    reg resend;
    wire [7:0] ram_data;
    wire ram_we = image_ready && image_valid;
    wire ram_en = state == SEND && byte_index >= PAYLOAD_START && byte_index < PACKET_CRC_START;
    wire [10:0] payload_offset = byte_index - PAYLOAD_START;
    wire [9:0] ram_addr = payload_offset[9:0];
    wire ack_matches = state == WAIT_ACK && ack_done && ack_crc_ok &&
                       ack_frame_id == frame_id && ack_packet_id == packet_id;
    wire ack_timeout = timer == ACK_TIMEOUT-1;
    function [7:0] header_byte;
        input [2:0] index;
        input [15:0] header_frame_id, header_packet_id;
        begin
            case (index)
                0: header_byte=header_frame_id[15:8]; 1: header_byte=header_frame_id[7:0];
                2: header_byte=header_packet_id[15:8]; 3: header_byte=header_packet_id[7:0];
                4: header_byte=8'h04; default: header_byte=8'h00;
            endcase
        end
    endfunction
    wire [31:0] next_header_crc = crc32_update(crc_state, header_byte(header_index, frame_id, packet_id));
    wire [31:0] next_payload_crc = crc32_update(crc_state, image_data);
    ip_packet_ram buffer (.clka(clk), .ena(1'b1), .wea(ram_we),
        .addra(load_index), .dina(image_data), .clkb(clk), .enb(ram_en),
        .addrb(ram_addr), .doutb(ram_data));
    assign image_ready = state == LOAD;
    assign tx_valid = state == SEND && settle == 0;
    assign tx_last = byte_index == PACKET_CRC_START + 3;
    always @* begin
        case (byte_index)
            0: tx_data=8'hD3; 1: tx_data=8'h91; 2: tx_data=8'hC5; 3: tx_data=8'hA7;
            4,5,6,7,8,9: tx_data=header_byte(byte_index-4, frame_id, packet_id);
            10: tx_data=header_crc[31:24]; 11: tx_data=header_crc[23:16];
            12: tx_data=header_crc[15:8]; 13: tx_data=header_crc[7:0];
            PACKET_CRC_START: tx_data=packet_crc[31:24];
            PACKET_CRC_START+11'd1: tx_data=packet_crc[23:16];
            PACKET_CRC_START+11'd2: tx_data=packet_crc[15:8];
            PACKET_CRC_START+11'd3: tx_data=packet_crc[7:0];
            default: tx_data=ram_data;
        endcase
    end
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; frame_id<=0; packets<=0; packet_id<=0; retry_count<=0;
            header_index<=0; load_index<=0; crc_state<=32'hFFFFFFFF; header_crc<=0; packet_crc<=0;
            timer<=0; settle<=0; resend<=0; done<=0; failed<=0; byte_index<=0;
            retransmissions<=0; timeouts<=0; ack_crc_errors<=0; ignored_acks<=0;
            acknowledged_packets<=0; exhausted_packets<=0;
        end else begin
            if (ack_done) begin
                if (!ack_crc_ok) ack_crc_errors <= ack_crc_errors + 1'b1;
                else if (!ack_matches) ignored_acks <= ignored_acks + 1'b1;
            end
            case (state)
                IDLE: if (start) begin
                    frame_id<=configured_frame_id; packets<=total_packets;
                    state<=total_packets == 0 ? FAILURE : HEADER;
                    failed<=total_packets == 0;
                    header_index<=0; crc_state<=32'hFFFFFFFF;
                end
                HEADER: begin
                    crc_state<=next_header_crc;
                    if (header_index==5) begin
                        header_crc<=~next_header_crc;
                        state<=LOAD; load_index<=0;
                    end
                    else header_index<=header_index+1'b1;
                end
                LOAD: if (ram_we) begin
                    crc_state<=next_payload_crc;
                    if (load_index==1023) begin
                        packet_crc<=~next_payload_crc;
                        byte_index<=0; settle<=2; state<=SEND;
                    end else load_index<=load_index+1'b1;
                end
                SEND: begin
                    if (settle!=0) settle<=settle-1'b1;
                    if (tx_valid && tx_ready) begin
                        if (tx_last) state<=WAIT_END;
                        else begin byte_index<=byte_index+1'b1; settle<=2; end
                    end
                end
                WAIT_END: if (tx_done) begin timer<=0; state<=WAIT_ACK; end
                WAIT_ACK: begin
                    if (ack_timeout && !ack_matches) timeouts<=timeouts+1'b1;
                    if (ack_matches || (ack_timeout && retry_count==MAX_RETRIES)) begin
                        if (ack_matches) acknowledged_packets<=acknowledged_packets+1'b1;
                        else exhausted_packets<=exhausted_packets+1'b1;
                        retry_count<=0;
                        if (packet_id+1==packets) begin done<=1; state<=FINISHED; end
                        else begin
                            packet_id<=packet_id+1'b1; timer<=0; resend<=0; state<=GUARD;
                        end
                    end else if (ack_timeout) begin
                        retry_count<=retry_count+1'b1; retransmissions<=retransmissions+1'b1;
                        timer<=0; resend<=1; state<=GUARD;
                    end else timer<=timer+1'b1;
                end
                GUARD: if (timer==GUARD_CLOCKS-1) begin
                    timer<=0;
                    if (resend) begin byte_index<=0; settle<=2; state<=SEND; end
                    else begin header_index<=0; crc_state<=32'hFFFFFFFF; state<=HEADER; end
                end else timer<=timer+1'b1;
                default: begin end
            endcase
        end
    end
endmodule
