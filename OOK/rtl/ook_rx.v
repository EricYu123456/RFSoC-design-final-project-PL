`timescale 1ns/1ps
module ook_rx #(
    // DDS Unit_Circle amplitude is approximately 16384; E1/2 = 4*A*A.
    parameter [35:0] THRESHOLD = 36'd1073741824
) (
    input wire clk, input wire rst, input wire symbol_end,
    input wire signed [15:0] sample,
    output reg bit_data, output reg bit_valid, output reg [35:0] bit_energy
);
    wire [31:0] power;
    reg symbol_end_d;
    reg [35:0] energy;
    wire [35:0] energy_next = energy + {4'b0, power};
    // IP is configured with exactly one register stage.
    ip_square square (.CLK(clk), .SCLR(rst), .A(sample), .B(sample), .P(power));
    always @(posedge clk) begin
        if (rst) begin
            symbol_end_d <= 0; energy <= 0; bit_data <= 0;
            bit_valid <= 0; bit_energy <= 0;
        end else begin
            symbol_end_d <= symbol_end;
            bit_valid <= symbol_end_d;
            if (symbol_end_d) begin
                bit_energy <= energy_next;
                bit_data <= energy_next > THRESHOLD;
                energy <= 0;
            end else energy <= energy_next;
        end
    end
endmodule
