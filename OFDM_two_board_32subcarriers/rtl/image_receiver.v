`timescale 1ns/1ps
module image_receiver #(
    parameter integer MAX_PACKETS = 300, parameter integer GUARD_CLOCKS = 64
) (
    input wire clk, input wire rst,
    input wire [15:0] configured_frame_id, input wire [15:0] total_packets,
    input wire packet_done, input wire crc_ok, input wire header_crc_ok,
    input wire [15:0] rx_frame_id, input wire [15:0] rx_packet_id,
    input wire [15:0] rx_payload_len,
    input wire payload_we, input wire [9:0] payload_addr, input wire [7:0] payload_data,
    output wire [7:0] sink_data, output wire [18:0] sink_addr,
    output wire sink_valid, output wire sink_quality, input wire sink_ready,
    output reg [7:0] ack_data, output wire ack_valid, input wire ack_ready,
    output wire ack_last, input wire ack_tx_done,
    // Simulation fault hook changes the ACK ID before its CRC is generated.
    input wire ack_override_en, input wire [15:0] ack_override_id,
    output reg [15:0] ack_packet_id, output reg [3:0] ack_byte_index,
    output reg [15:0] unique_packets, output reg [31:0] duplicates,
    output reg [31:0] crc_errors, output reg [31:0] header_errors,
    output reg commit_done, output reg [15:0] committed_id,
    output reg [3:0] state
);
    `include "crc32_functions.vh"
    localparam IDLE=0, READ_RAM=1, COMMIT=2, ACK_CRC=3, GUARD=4, ACK_SEND=5,
               ACK_END=6, COMMIT_QUALITY=7;
    reg [MAX_PACKETS-1:0] seen, verified;
    reg [15:0] current_packet, ack_frame_id;
    reg current_crc_ok;
    reg [9:0] read_index;
    reg [2:0] header_index;
    reg [31:0] crc_state, ack_crc, timer;
    wire [7:0] ram_data;
    wire ram_en = state==READ_RAM || state==COMMIT;
    wire header_valid = header_crc_ok && rx_frame_id==configured_frame_id &&
                        rx_packet_id<total_packets && rx_packet_id<MAX_PACKETS &&
                        rx_payload_len==1024;
    function [7:0] ack_header_byte;
        input [2:0] index;
        begin
            case(index)
                0: ack_header_byte=ack_frame_id[15:8]; 1: ack_header_byte=ack_frame_id[7:0];
                2: ack_header_byte=ack_packet_id[15:8]; default: ack_header_byte=ack_packet_id[7:0];
            endcase
        end
    endfunction
    ip_packet_ram buffer (.clka(clk), .ena(1'b1), .wea(payload_we),
        .addra(payload_addr), .dina(payload_data), .clkb(clk), .enb(ram_en),
        .addrb(read_index), .doutb(ram_data));
    // A packet's quality byte is written after all of its payload bytes.
    assign sink_quality = state==COMMIT_QUALITY;
    assign sink_data = sink_quality ? (current_crc_ok ? 8'd2 : 8'd1) : ram_data;
    assign sink_addr = sink_quality ? {3'b0, current_packet} : (current_packet * 1024) + read_index;
    assign sink_valid = state==COMMIT || sink_quality;
    assign ack_valid = state==ACK_SEND;
    assign ack_last = ack_byte_index==11;
    always @* begin
        case (ack_byte_index)
            0: ack_data=8'hAC; 1: ack_data=8'h4B; 2: ack_data=8'h7E; 3: ack_data=8'h29;
            4,5,6,7: ack_data=ack_header_byte(ack_byte_index-4);
            8: ack_data=ack_crc[31:24]; 9: ack_data=ack_crc[23:16];
            10: ack_data=ack_crc[15:8]; default: ack_data=ack_crc[7:0];
        endcase
    end
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; seen<=0; verified<=0; current_packet<=0; current_crc_ok<=0; read_index<=0;
            ack_frame_id<=0; ack_packet_id<=0; ack_byte_index<=0;
            header_index<=0; crc_state<=32'hFFFFFFFF; ack_crc<=0; timer<=0;
            unique_packets<=0; duplicates<=0; crc_errors<=0; header_errors<=0;
            commit_done<=0; committed_id<=0;
        end else begin
            commit_done<=0;
            if (packet_done) begin
                if (!crc_ok) crc_errors<=crc_errors+1'b1;
                if (!header_valid) header_errors<=header_errors+1'b1;
            end
            case (state)
                IDLE: if (packet_done && header_valid) begin
                    if (crc_ok) begin
                        ack_frame_id<=rx_frame_id;
                        ack_packet_id<=ack_override_en ? ack_override_id : rx_packet_id;
                        header_index<=0; crc_state<=32'hFFFFFFFF;
                    end
                    if (!verified[rx_packet_id]) begin
                        current_packet<=rx_packet_id;
                        current_crc_ok<=crc_ok;
                        read_index<=0; state<=READ_RAM;
                    end else if (crc_ok) begin
                        duplicates<=duplicates+1'b1; state<=ACK_CRC;
                    end
                end
                READ_RAM: state<=COMMIT;
                COMMIT: if (sink_ready) begin
                    if (read_index==1023) state<=COMMIT_QUALITY;
                    else begin read_index<=read_index+1'b1; state<=READ_RAM; end
                end
                COMMIT_QUALITY: if (sink_ready) begin
                    if (!seen[current_packet]) begin
                        seen[current_packet]<=1; unique_packets<=unique_packets+1'b1;
                    end
                    if (current_crc_ok) verified[current_packet]<=1;
                    commit_done<=1; committed_id<=current_packet;
                    state<=current_crc_ok ? ACK_CRC : IDLE;
                end
                ACK_CRC: begin
                    crc_state<=crc32_update(crc_state,ack_header_byte(header_index));
                    if (header_index==3) begin
                        ack_crc<=~crc32_update(crc_state,ack_header_byte(header_index));
                        timer<=0; state<=GUARD;
                    end else header_index<=header_index+1'b1;
                end
                GUARD: if (timer==GUARD_CLOCKS-1) begin
                    ack_byte_index<=0; state<=ACK_SEND;
                end else timer<=timer+1'b1;
                ACK_SEND: if (ack_valid && ack_ready) begin
                    if (ack_last) state<=ACK_END;
                    else ack_byte_index<=ack_byte_index+1'b1;
                end
                ACK_END: if (ack_tx_done) state<=IDLE;
                default: state<=IDLE;
            endcase
        end
    end
endmodule
