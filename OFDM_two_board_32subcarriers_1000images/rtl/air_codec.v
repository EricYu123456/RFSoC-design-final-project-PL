`timescale 1ns/1ps
// Byte streams between the TDD controller and OFDM PHY. A header CRC protects
// addressing even when the payload is damaged. A second CRC protects DATA.
module air_codec(
    input wire clk, rst, launch,
    input wire [7:0] kind, mask,
    input wire [31:0] session,
    input wire [15:0] frame, number,
    output wire [7:0] tx_byte, output reg tx_valid, output wire tx_last,
    input wire tx_ready, output wire [18:0] source_address, input wire [7:0] source_byte,
    input wire rx_start, rx_valid, input wire [7:0] rx_byte,
    output reg received, payload_good,
    output wire [7:0] rx_kind, rx_mask, output wire [31:0] rx_session,
    output wire [15:0] rx_frame, rx_number,
    input wire [9:0] scratch_addr, output reg [7:0] scratch_data
);
    `include "air_protocol.vh"
    `include "crc32_functions.vh"
    reg [87:0] header_out, header_in;
    reg [10:0] position, rx_pos;
    reg [31:0] crc_out, crc_in, check_out, check_in;
    reg data_out, listening, header_ok;
    wire [10:0] next_position=position+{10'd0,tx_valid && tx_ready};
    wire [9:0] source_offset=next_position[9:0]-10'd15;
    assign source_address={number[8:0],source_offset};
    wire payload=position>=15 && position<1039;
    assign tx_byte=position<11 ? header_out[87:80] :
        (data_out && payload ? source_byte : check_out[31:24]);
    assign tx_last=position==(data_out ? DATA_BYTES-1 : CONTROL_BYTES-1);
    wire [31:0] next_crc=crc32_update(crc_out,tx_byte);
    always @(posedge clk) begin
        if (rst) begin tx_valid<=0; position<=0; end
        else if (launch) begin
            header_out<={kind==DATA ? 8'hd3 : 8'hac,kind,session,frame,number,mask};
            data_out<=kind==DATA; position<=0; tx_valid<=1; crc_out<=32'hffffffff;
        end else if (tx_valid && tx_ready) begin
            position<=position+1'b1;
            if (position<11 || payload) begin
                // The checksum bytes in positions 11..14 precede the payload.
                if (position<11) header_out<={header_out[79:0],8'b0};
                crc_out<=next_crc;
                if (position==10 || position==1038) check_out<=~next_crc;
            end else check_out<={check_out[23:0],8'b0};
            if (tx_last) tx_valid<=0;
        end
    end
    assign {rx_kind,rx_session,rx_frame,rx_number,rx_mask}=header_in[79:0];
    wire is_data=header_in[87:80]==8'hd3 && rx_kind==DATA;
    wire is_control=header_in[87:80]==8'hac && rx_kind>=OPEN && rx_kind<=CLOSED;
    wire crc_matches={check_in[23:0],rx_byte}==~crc_in;
    (* ram_style="block" *) reg [7:0] scratch [0:1023];
    wire [9:0] offset=rx_pos[9:0]-10'd15;
    always @(posedge clk) begin
        scratch_data<=scratch[scratch_addr];
        if (listening && rx_valid && rx_pos>=15 && rx_pos<1039)
            scratch[offset]<=rx_byte;
    end
    always @(posedge clk) begin
        received<=0;
        if (rst || rx_start) begin
            listening<=!rst; rx_pos<=0; crc_in<=32'hffffffff; header_ok<=0;
        end else if (listening && rx_valid) begin
            rx_pos<=rx_pos+1'b1;
            if (rx_pos<11) header_in<={header_in[79:0],rx_byte};
            if (rx_pos<11 || (rx_pos>=15 && rx_pos<1039))
                crc_in<=crc32_update(crc_in,rx_byte);
            else check_in<={check_in[23:0],rx_byte};
            if (rx_pos==14) begin
                header_ok<=crc_matches;
                if (!is_data || !crc_matches) begin
                    listening<=0; received<=is_control && crc_matches; payload_good<=1;
                end
            end
            if (rx_pos==1042) begin
                received<=header_ok && is_data; payload_good<=crc_matches; listening<=0;
            end
        end
    end
endmodule

