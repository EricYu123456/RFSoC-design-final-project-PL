`timescale 1ns/1ps

// The PS must be able to read its AXI peripherals even when the RF clock stops.
// Only these command and status handshakes cross the two clock domains.
module ofdm_rf_host #(
    parameter integer STATUS_TIMEOUT_CYCLES = 100000  // 1 ms at 100 MHz
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 ps_clk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ps_clk, ASSOCIATED_RESET ps_aresetn" *)
    input wire ps_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 ps_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME ps_aresetn, POLARITY ACTIVE_LOW" *)
    input wire ps_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 rf_clk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rf_clk, ASSOCIATED_RESET rf_aresetn" *)
    input wire rf_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rf_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rf_aresetn, POLARITY ACTIVE_LOW" *)
    input wire rf_aresetn,
    input wire [31:0] ps_control,
    output wire [31:0] ps_status, ps_levels, ps_retries, ps_health, ps_applied_control,
    output reg [31:0] rf_control = 32'h04004001,
    input wire [31:0] rf_status, rf_levels, rf_retries
);
    localparam [31:0] RESET_CONTROL = 32'h04004001;
    localparam [31:0] HOST_SIGNATURE = 32'h4f4f4b20;
    localparam integer AGE_WIDTH = $clog2(STATUS_TIMEOUT_CYCLES + 1);
    localparam [AGE_WIDTH-1:0] STATUS_TIMEOUT = STATUS_TIMEOUT_CYCLES;

    reg [31:0] command_word = RESET_CONTROL;
    reg command_send = 0;
    wire command_ack, command_valid;
    wire [31:0] received_command;

    // Keep data stable until acknowledgement. Refresh the current command so
    // it is delivered again if the RF datapath leaves reset after the PS.
    always @(posedge ps_clk) begin
        if (command_send) begin
            if (command_ack)
                command_send <= 0;
        end else if (!command_ack && ps_aresetn) begin
            command_word <= ps_control;
            command_send <= 1;
        end
    end

    xpm_cdc_handshake #(
        .DEST_EXT_HSK(0), .DEST_SYNC_FF(4), .SRC_SYNC_FF(4),
        .INIT_SYNC_FF(1), .SIM_ASSERT_CHK(1), .WIDTH(32)
    ) command_cdc (
        .src_clk(ps_clk), .src_in(command_word), .src_send(command_send), .src_rcv(command_ack),
        .dest_clk(rf_clk), .dest_out(received_command), .dest_req(command_valid), .dest_ack(1'b0)
    );

    always @(posedge rf_clk) begin
        if (!rf_aresetn)
            rf_control <= RESET_CONTROL;
        else if (command_valid)
            rf_control <= received_command;
    end

    reg [128:0] snapshot_word = 0;
    reg snapshot_send = 0;
    wire snapshot_ack, snapshot_valid;
    wire [128:0] received_snapshot;

    // Echo the command actually applied in the RF domain. The host waits for
    // this echo before changing the command, so the start edge cannot be lost.
    always @(posedge rf_clk) begin
        if (snapshot_send) begin
            if (snapshot_ack)
                snapshot_send <= 0;
        end else if (!snapshot_ack) begin
            snapshot_word <= {rf_aresetn, rf_control, rf_status, rf_levels, rf_retries};
            snapshot_send <= 1;
        end
    end

    xpm_cdc_handshake #(
        .DEST_EXT_HSK(0), .DEST_SYNC_FF(4), .SRC_SYNC_FF(4),
        .INIT_SYNC_FF(1), .SIM_ASSERT_CHK(1), .WIDTH(129)
    ) snapshot_cdc (
        .src_clk(rf_clk), .src_in(snapshot_word), .src_send(snapshot_send), .src_rcv(snapshot_ack),
        .dest_clk(ps_clk), .dest_out(received_snapshot), .dest_req(snapshot_valid), .dest_ack(1'b0)
    );

    reg [AGE_WIDTH-1:0] age = STATUS_TIMEOUT;
    always @(posedge ps_clk) begin
        if (!ps_aresetn)
            age <= STATUS_TIMEOUT;
        else if (snapshot_valid)
            age <= 0;
        else if (age < STATUS_TIMEOUT)
            age <= age + 1'b1;
    end

    assign {ps_applied_control, ps_status, ps_levels, ps_retries} = received_snapshot[127:0];
    assign ps_health = HOST_SIGNATURE | {31'b0, ps_aresetn && age < STATUS_TIMEOUT && received_snapshot[128]};
endmodule

