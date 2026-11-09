`timescale 1ns/1ps
// One pending byte decouples byte handshakes from the continuous sample clock.
module ook_tx (
    input wire clk, input wire rst, input wire symbol_end,
    input wire [7:0] s_data, input wire s_valid, output wire s_ready,
    input wire s_last,
    output wire signed [15:0] sample,
    output wire busy, output reg frame_start, output reg frame_done,
    output reg underrun, output wire serial_bit,
    output wire signed [15:0] carrier
);
    wire carrier_valid;
    reg pending_valid, pending_last;
    reg [7:0] pending_data, shift_data;
    reg active, current_last;
    reg [2:0] bit_index;

    ip_carrier dds (.aclk(clk), .aresetn(~rst),
        .m_axis_data_tvalid(carrier_valid), .m_axis_data_tdata(carrier));
    assign s_ready = !pending_valid && carrier_valid && !rst;
    assign busy = active || pending_valid;
    assign serial_bit = active && shift_data[7];
    assign sample = (serial_bit && carrier_valid) ? carrier : 16'sd0;

    always @(posedge clk) begin
        if (rst) begin
            pending_valid <= 0; pending_last <= 0; pending_data <= 0;
            active <= 0; current_last <= 0; shift_data <= 0; bit_index <= 7;
            frame_start <= 0; frame_done <= 0; underrun <= 0;
        end else begin
            frame_start <= 0; frame_done <= 0;
            if (s_valid && s_ready) begin
                pending_data <= s_data; pending_last <= s_last; pending_valid <= 1;
            end
            if (symbol_end) begin
                if (!active) begin
                    if (pending_valid) begin
                        shift_data <= pending_data; current_last <= pending_last;
                        pending_valid <= 0; active <= 1; bit_index <= 7;
                        frame_start <= 1;
                    end
                end else if (bit_index != 0) begin
                    shift_data <= {shift_data[6:0], 1'b0}; bit_index <= bit_index - 1'b1;
                end else if (current_last) begin
                    active <= 0; shift_data <= 0; frame_done <= 1;
                end else if (pending_valid) begin
                    shift_data <= pending_data; current_last <= pending_last;
                    pending_valid <= 0; bit_index <= 7;
                end else begin
                    active <= 0; shift_data <= 0; underrun <= 1;
                end
            end
        end
    end
endmodule
