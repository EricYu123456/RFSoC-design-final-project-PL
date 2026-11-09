`timescale 1ns/1ps
// Time division loopback: BOTH data and ACK traverse the external DAC/ADC cable.
module rf_image_link # (parameter integer SYMBOL_CLOCKS=64) (
    input wire clk, input wire rst, input wire start,
    input wire [15:0] total_packets,
    input wire [7:0] image_data, input wire image_valid, output wire image_ready,
    output wire [7:0] sink_data, output wire [18:0] sink_addr,
    output wire sink_valid, input wire sink_ready,
    input wire [31:0] adc_i, input wire [31:0] adc_q, input wire adc_valid,
    input wire [15:0] threshold,
    output wire envelope, output wire done, output wire failed,
    output wire [15:0] magnitude, output wire [15:0] peak,
    output wire [31:0] retransmissions, output wire phy_fault
);
    reg [15:0] tx_phase;
    wire symbol_end = tx_phase==SYMBOL_CLOCKS-1;
    always @(posedge clk) begin
        if (rst || symbol_end) tx_phase<=0;
        else tx_phase<=tx_phase+1'b1;
    end
    wire [7:0] data_byte,ack_byte;
    wire data_valid,data_ready,data_last,data_done,data_env,data_busy,data_underrun;
    wire ack_valid,ack_ready,ack_last,ack_done,ack_env,ack_busy,ack_underrun;
    wire rx_bit,rx_valid,data_packet_done,data_crc_ok,ack_packet_done,ack_crc_ok;
    wire [15:0] data_frame,data_packet,data_length,ack_frame,ack_packet;
    wire payload_we;
    wire [9:0] payload_addr;
    wire [7:0] payload_data;
    assign envelope = data_env | ack_env;
    assign phy_fault = data_underrun | ack_underrun | (data_busy && ack_busy);
    image_sender #(.ACK_TIMEOUT(65536),.GUARD_CLOCKS(512)) sender (
        .clk(clk),.rst(rst),.start(start),.configured_frame_id(16'd0),.total_packets(total_packets),
        .image_data(image_data),.image_valid(image_valid),.image_ready(image_ready),
        .tx_data(data_byte),.tx_valid(data_valid),.tx_ready(data_ready),.tx_last(data_last),.tx_done(data_done),
        .ack_done(ack_packet_done),.ack_crc_ok(ack_crc_ok),.ack_frame_id(ack_frame),.ack_packet_id(ack_packet),
        .done(done),.failed(failed),.retransmissions(retransmissions));
    rf_ook_tx data_tx (.clk(clk),.rst(rst),.symbol_end(symbol_end),
        .s_data(data_byte),.s_valid(data_valid),.s_ready(data_ready),.s_last(data_last),
        .envelope(data_env),.busy(data_busy),.frame_done(data_done),.underrun(data_underrun));
    rf_ook_tx ack_tx (.clk(clk),.rst(rst),.symbol_end(symbol_end),
        .s_data(ack_byte),.s_valid(ack_valid),.s_ready(ack_ready),.s_last(ack_last),
        .envelope(ack_env),.busy(ack_busy),.frame_done(ack_done),.underrun(ack_underrun));
    rf_ook_rx #(.SYMBOL_CLOCKS(SYMBOL_CLOCKS)) rx (.clk(clk),.rst(rst),
        .adc_i(adc_i),.adc_q(adc_q),.adc_valid(adc_valid),.threshold(threshold),
        .bit_data(rx_bit),.bit_valid(rx_valid),.magnitude(magnitude),.peak(peak));
    packet_rx data_parser (.clk(clk),.rst(rst),.bit_data(rx_bit),.bit_valid(rx_valid),
        .packet_done(data_packet_done),.crc_ok(data_crc_ok),.frame_id(data_frame),.packet_id(data_packet),
        .payload_len(data_length),.payload_we(payload_we),.payload_addr(payload_addr),.payload_data(payload_data));
    packet_rx #(.SYNC_WORD(32'hAC4B7E29),.HEADER_BYTES(4),.PAYLOAD_BYTES(0)) ack_parser (
        .clk(clk),.rst(rst),.bit_data(rx_bit),.bit_valid(rx_valid),.packet_done(ack_packet_done),
        .crc_ok(ack_crc_ok),.frame_id(ack_frame),.packet_id(ack_packet));
    image_receiver #(.GUARD_CLOCKS(512)) receiver (.clk(clk),.rst(rst),
        .configured_frame_id(16'd0),.total_packets(total_packets),
        .packet_done(data_packet_done),.crc_ok(data_crc_ok),.rx_frame_id(data_frame),
        .rx_packet_id(data_packet),.rx_payload_len(data_length),.payload_we(payload_we),
        .payload_addr(payload_addr),.payload_data(payload_data),.sink_data(sink_data),.sink_addr(sink_addr),
        .sink_valid(sink_valid),.sink_ready(sink_ready),.ack_data(ack_byte),.ack_valid(ack_valid),
        .ack_ready(ack_ready),.ack_last(ack_last),.ack_tx_done(ack_done),
        .ack_override_en(1'b0),.ack_override_id(16'd0));
endmodule
