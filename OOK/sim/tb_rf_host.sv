`timescale 1ns/1ps

module tb_rf_host;
    localparam integer WATCHDOG_CYCLES = 128;
    localparam [31:0] SIGNATURE = 32'h4f4f4b20;
    localparam [31:0] RESET_CONTROL = 32'h04004001;

    reg ps_clk = 0, rf_clk = 0;
    reg ps_aresetn = 0, rf_aresetn = 0, rf_clock_running = 0;
    reg [31:0] command = RESET_CONTROL;
    wire [31:0] rf_control, health, applied, status, levels, retries;
    reg start_d = 0;
    integer starts = 0;

    always #5 ps_clk = ~ps_clk;
    always #3.255 rf_clk = rf_clock_running ? ~rf_clk : 1'b0;
    always @(posedge rf_clk) begin
        start_d <= rf_control[1] && !rf_control[0] && rf_aresetn;
        if (rf_aresetn && !rf_control[0] && rf_control[1] && !start_d)
            starts <= starts + 1;
    end

    ook_rf_host #(.STATUS_TIMEOUT_CYCLES(WATCHDOG_CYCLES)) dut (
        .ps_clk(ps_clk), .ps_aresetn(ps_aresetn), .rf_clk(rf_clk), .rf_aresetn(rf_aresetn),
        .ps_control(command), .ps_health(health), .ps_applied_control(applied),
        .rf_control(rf_control), .ps_status(status), .ps_levels(levels), .ps_retries(retries),
        .rf_status(32'h9e123408), .rf_levels(32'h56780456), .rf_retries(32'd7)
    );

    task automatic apply(input [31:0] word);
        @(negedge ps_clk);
        command = word;
        wait (applied === word && health === (SIGNATURE | 1));
        @(negedge ps_clk);
        if (rf_control !== word)
            $fatal(1, "Host echoed a command that RF had not applied");
    endtask

    initial begin
        repeat (8) @(negedge ps_clk);
        ps_aresetn = 1;
        repeat (4) @(negedge ps_clk);
        if (health !== SIGNATURE)
            $fatal(1, "PS signature must be readable with RF clock absent at boot");

        rf_clock_running = 1;
        repeat (16) @(negedge rf_clk);
        rf_aresetn = 1;
        apply(RESET_CONTROL);
        if ({status, levels, retries} !== {32'h9e123408, 32'h56780456, 32'd7})
            $fatal(1, "RF snapshot did not cross coherently");
        apply(32'h04324000);
        apply(32'h04324002);
        apply(32'h04324000);
        if (starts != 1)
            $fatal(1, "Expected exactly one start edge, got %d", starts);

        rf_aresetn = 0;
        rf_clock_running = 0;
        command = RESET_CONTROL;
        repeat (WATCHDOG_CYCLES + 8) @(negedge ps_clk);
        if (health !== SIGNATURE)
            $fatal(1, "Stopped RF clock did not expire the PS watchdog");

        rf_clock_running = 1;
        repeat (24) @(negedge rf_clk);
        if (health !== SIGNATURE)
            $fatal(1, "Host reported RF ready during RF reset");
        rf_aresetn = 1;
        apply(RESET_CONTROL);
        if (starts != 1)
            $fatal(1, "RF clock recovery replayed a start edge");
        $display("RF_HOST_SIM_PASS: cold boot, command echo, stopped RF clock, reset and recovery");
        $finish;
    end

    initial begin
        #50000;
        $fatal(1, "Host clock crossing test timed out");
    end
endmodule
