`timescale 1ns/1ps
// Synthesis wrapper: ideal wired loopback, all simulation fault hooks disabled.
module image_link_synth (
    input wire clk, input wire rst, input wire start,
    input wire [7:0] image_data, input wire image_valid, output wire image_ready,
    output wire [7:0] sink_data, output wire [18:0] sink_addr,
    output wire sink_valid, input wire sink_ready, output wire done, output wire failed
);
    wire signed [15:0] forward_wave, reverse_wave;
    image_link core (.clk(clk), .rst(rst), .start(start), .frame_id(16'd0), .total_packets(16'd300),
        .image_data(image_data), .image_valid(image_valid), .image_ready(image_ready),
        .sink_data(sink_data), .sink_addr(sink_addr), .sink_valid(sink_valid), .sink_ready(sink_ready),
        .data_wave(forward_wave), .data_channel(forward_wave),
        .ack_wave(reverse_wave), .ack_channel(reverse_wave),
        .data_byte_xor(8'd0), .ack_byte_xor(8'd0), .ack_override_en(1'b0), .ack_override_id(16'd0),
        .done(done), .failed(failed));
endmodule
