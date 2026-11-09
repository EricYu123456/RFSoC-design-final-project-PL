`timescale 1ns/1ps
// A and B are logical endpoints; the testbench supplies the two sample channels.
module image_link (
    input wire clk, input wire rst, input wire start,
    input wire [15:0] frame_id, input wire [15:0] total_packets,
    input wire [7:0] image_data, input wire image_valid, output wire image_ready,
    output wire [7:0] sink_data, output wire [18:0] sink_addr,
    output wire sink_valid, input wire sink_ready,
    output wire signed [15:0] data_wave, input wire signed [15:0] data_channel,
    output wire signed [15:0] ack_wave, input wire signed [15:0] ack_channel,
    // Test hooks are tied to zero in image_link_synth.
    input wire [7:0] data_byte_xor, input wire [7:0] ack_byte_xor,
    input wire ack_override_en, input wire [15:0] ack_override_id,
    output wire done, output wire failed
);
    reg [3:0] sample_phase;
    wire symbol_end = sample_phase==15;
    always @(posedge clk) begin
        if (rst) sample_phase<=0;
        else sample_phase<=sample_phase+1'b1;
    end
    wire [7:0] data_byte, ack_byte;
    wire data_valid, data_ready, data_last, data_tx_done;
    wire ack_valid, ack_ready, ack_last, ack_tx_done;
    wire data_busy, ack_busy, data_start, ack_start, data_underrun, ack_underrun;
    wire data_bit, data_bit_valid, ack_bit, ack_bit_valid;
    wire [35:0] data_energy, ack_energy;
    wire data_packet_done, data_crc_ok, ack_packet_done, ack_crc_ok;
    wire [15:0] data_frame_id, data_packet_id, data_payload_len;
    wire [15:0] ack_frame_id, ack_packet_id;
    wire payload_we;
    wire [9:0] payload_addr;
    wire [7:0] payload_data;

    image_sender sender (.clk(clk), .rst(rst), .start(start),
        .configured_frame_id(frame_id), .total_packets(total_packets),
        .image_data(image_data), .image_valid(image_valid), .image_ready(image_ready),
        .tx_data(data_byte), .tx_valid(data_valid), .tx_ready(data_ready),
        .tx_last(data_last), .tx_done(data_tx_done),
        .ack_done(ack_packet_done), .ack_crc_ok(ack_crc_ok),
        .ack_frame_id(ack_frame_id), .ack_packet_id(ack_packet_id), .done(done), .failed(failed));
    ook_tx data_phy (.clk(clk), .rst(rst), .symbol_end(symbol_end),
        .s_data(data_byte ^ data_byte_xor), .s_valid(data_valid), .s_ready(data_ready),
        .s_last(data_last), .sample(data_wave), .busy(data_busy), .frame_start(data_start),
        .frame_done(data_tx_done), .underrun(data_underrun));
    ook_rx data_demod (.clk(clk), .rst(rst), .symbol_end(symbol_end),
        .sample(data_channel), .bit_data(data_bit), .bit_valid(data_bit_valid), .bit_energy(data_energy));
    packet_rx data_parser (.clk(clk), .rst(rst), .bit_data(data_bit), .bit_valid(data_bit_valid),
        .packet_done(data_packet_done), .crc_ok(data_crc_ok),
        .frame_id(data_frame_id), .packet_id(data_packet_id), .payload_len(data_payload_len),
        .payload_we(payload_we), .payload_addr(payload_addr), .payload_data(payload_data));
    image_receiver receiver (.clk(clk), .rst(rst), .configured_frame_id(frame_id),
        .total_packets(total_packets), .packet_done(data_packet_done), .crc_ok(data_crc_ok),
        .rx_frame_id(data_frame_id), .rx_packet_id(data_packet_id), .rx_payload_len(data_payload_len),
        .payload_we(payload_we), .payload_addr(payload_addr), .payload_data(payload_data),
        .sink_data(sink_data), .sink_addr(sink_addr), .sink_valid(sink_valid), .sink_ready(sink_ready),
        .ack_data(ack_byte), .ack_valid(ack_valid), .ack_ready(ack_ready), .ack_last(ack_last),
        .ack_tx_done(ack_tx_done), .ack_override_en(ack_override_en), .ack_override_id(ack_override_id));
    ook_tx ack_phy (.clk(clk), .rst(rst), .symbol_end(symbol_end),
        .s_data(ack_byte ^ ack_byte_xor), .s_valid(ack_valid), .s_ready(ack_ready),
        .s_last(ack_last), .sample(ack_wave), .busy(ack_busy), .frame_start(ack_start),
        .frame_done(ack_tx_done), .underrun(ack_underrun));
    ook_rx ack_demod (.clk(clk), .rst(rst), .symbol_end(symbol_end),
        .sample(ack_channel), .bit_data(ack_bit), .bit_valid(ack_bit_valid), .bit_energy(ack_energy));
    packet_rx #(.SYNC_WORD(32'hAC4B7E29), .HEADER_BYTES(4), .PAYLOAD_BYTES(0)) ack_parser (
        .clk(clk), .rst(rst), .bit_data(ack_bit), .bit_valid(ack_bit_valid),
        .packet_done(ack_packet_done), .crc_ok(ack_crc_ok),
        .frame_id(ack_frame_id), .packet_id(ack_packet_id));
endmodule
