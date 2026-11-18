`timescale 1ns/1ps
// One endpoint sends data and receives ACKs. The other receives data and sends ACKs.
module rf_image_link (
    input wire clk, input wire rst, input wire start,
    input wire receiver_role, input wire listen,
    input wire [15:0] total_packets,
    input wire [7:0] image_data, input wire image_valid, output wire image_ready,
    output wire [7:0] sink_data, output wire [18:0] sink_addr,
    output wire sink_valid, sink_quality, input wire sink_ready,
    input wire [31:0] adc_i, input wire [31:0] adc_q, input wire adc_valid,
    input wire [15:0] threshold,
    output wire signed [12:0] tx_i, output wire signed [12:0] tx_q,
    output wire done, output wire failed,
    output wire [15:0] magnitude, output wire [15:0] peak,
    output wire [31:0] retransmissions, output wire phy_fault,
    output wire [15:0] rx_present_packets, tx_exhausted_packets
);
    wire [7:0] data_byte, ack_byte;
    wire data_valid, data_last, ack_valid, ack_last, tx_ready, tx_done;
    wire data_packet_done, data_crc_ok, header_crc_ok, ack_packet_done, ack_crc_ok;
    wire [15:0] data_frame, data_packet, data_length, ack_frame, ack_packet;
    wire payload_we;
    wire [9:0] payload_addr;
    wire [7:0] payload_data;
    wire [31:0] tx_retries, rx_duplicates;
    wire tx_busy;
    assign retransmissions = receiver_role ? rx_duplicates : tx_retries;

    image_sender #(.ACK_TIMEOUT(65536),.GUARD_CLOCKS(512)) sender (
        .clk(clk),.rst(rst),.start(start),.configured_frame_id(16'd0),.total_packets(total_packets),
        .image_data(image_data),.image_valid(image_valid),.image_ready(image_ready),
        .tx_data(data_byte),.tx_valid(data_valid),.tx_ready(tx_ready && !receiver_role),
        .tx_last(data_last),.tx_done(tx_done && !receiver_role),
        .ack_done(ack_packet_done),.ack_crc_ok(ack_crc_ok),.ack_frame_id(ack_frame),.ack_packet_id(ack_packet),
        .done(done),.failed(failed),.retransmissions(tx_retries),.exhausted_packets(tx_exhausted_packets));
    ofdm_tx tx (.clk(clk),.rst(rst),
        .s_data(receiver_role ? ack_byte : data_byte),
        .s_valid(receiver_role ? ack_valid : data_valid),
        .s_last(receiver_role ? ack_last : data_last),.s_ready(tx_ready),
        .sample_i(tx_i),.sample_q(tx_q),.busy(tx_busy),
        .frame_done(tx_done),.underrun(phy_fault));

    wire frame_start, is_ack, rx_byte_valid;
    wire [7:0] rx_byte;
    ofdm_rx rx (.clk(clk),.rst(rst),.adc_i(adc_i),.adc_q(adc_q),.adc_valid(adc_valid),
        .threshold(threshold),.frame_start(frame_start),.is_ack(is_ack),
        .byte_data(rx_byte),.byte_valid(rx_byte_valid),.magnitude(magnitude),.peak(peak));
    // Feed the existing CRC parsers in MSB-first order. A newly acquired
    // burst clears incomplete packets left by an interrupted transmission.
    reg [7:0] rx_shift;
    reg [3:0] rx_bits_left;
    wire rx_valid = rx_bits_left != 0;
    always @(posedge clk) begin
        if (rst || frame_start) begin
            rx_shift <= 0;
            rx_bits_left <= 0;
        end else if (rx_byte_valid) begin
            rx_shift <= rx_byte;
            rx_bits_left <= 8;
        end else if (rx_valid) begin
            rx_shift <= {rx_shift[6:0],1'b0};
            rx_bits_left <= rx_bits_left - 1'b1;
        end
    end

    packet_rx data_parser (.clk(clk),.rst(rst || frame_start || !listen || !receiver_role),
        .bit_data(rx_shift[7]),.bit_valid(rx_valid && !is_ack),
        .packet_done(data_packet_done),.crc_ok(data_crc_ok),.header_crc_ok(header_crc_ok),.frame_id(data_frame),.packet_id(data_packet),
        .payload_len(data_length),.payload_we(payload_we),.payload_addr(payload_addr),.payload_data(payload_data));
    packet_rx #(.SYNC_WORD(32'hAC4B7E29),.HEADER_BYTES(4),.PAYLOAD_BYTES(0)) ack_parser (
        .clk(clk),.rst(rst || frame_start || !listen || receiver_role),.bit_data(rx_shift[7]),.bit_valid(rx_valid && is_ack),
        .packet_done(ack_packet_done),.crc_ok(ack_crc_ok),.frame_id(ack_frame),.packet_id(ack_packet));
    image_receiver #(.GUARD_CLOCKS(512)) receiver (.clk(clk),.rst(rst),
        .configured_frame_id(16'd0),.total_packets(total_packets),
        .packet_done(data_packet_done),.crc_ok(data_crc_ok),.header_crc_ok(header_crc_ok),.rx_frame_id(data_frame),
        .rx_packet_id(data_packet),.rx_payload_len(data_length),.payload_we(payload_we),
        .payload_addr(payload_addr),.payload_data(payload_data),.sink_data(sink_data),.sink_addr(sink_addr),
        .sink_valid(sink_valid),.sink_quality(sink_quality),.sink_ready(sink_ready),.ack_data(ack_byte),.ack_valid(ack_valid),
        .ack_ready(tx_ready && receiver_role),.ack_last(ack_last),.ack_tx_done(tx_done && receiver_role),
        .unique_packets(rx_present_packets),.duplicates(rx_duplicates),
        .ack_override_en(1'b0),.ack_override_id(16'd0));
endmodule
