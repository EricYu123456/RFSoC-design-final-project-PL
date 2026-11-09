`timescale 1ns/1ps

module tb_rf_board;
    parameter integer PACKETS = 3;
    parameter integer CHANNEL_PROFILE = 0;
    localparam integer BYTES = PACKETS * 1024;
    localparam integer SYMBOL_CLOCKS = 12 * 8;
    localparam integer DELAY_CLOCKS = CHANNEL_PROFILE ? 113 : 37;
    localparam [6:0] STATUS_ID = 7'h3d;
    localparam [31:0] RESET_CONTROL = 32'h04004001;

    reg clk = 0;
    always #3.255 clk = ~clk;
    reg aresetn = 0;
    reg [31:0] control = RESET_CONTROL;
    wire [31:0] status, levels, retries;
    wire [3:0] leds;
    wire [31:0] adc_i, adc_q;
    wire adc_i_ready, adc_q_ready;
    wire [63:0] dac_data;
    wire dac_valid;
    wire src_clk, src_rst, src_en, dst_clk, dst_rst, dst_en;
    wire [3:0] src_we, dst_we;
    wire [31:0] src_addr, src_din, dst_addr, dst_din;
    reg [31:0] src_dout = 0;
    reg [7:0] source [0:307199];
    reg [7:0] destination [0:BYTES-1];

    integer drop_ack = 0;
    integer corrupt_ack = 0;
    integer disconnected = 0;
    string output_path;
    integer cycles = 0, sink_writes = 0;
    integer ack_attempts = 0, ack_byte_index = 0;
    integer dropped_beats = 0, corrupted_beats = 0;
    reg [3:0] iq_signs = 0;
    reg ack_busy_d = 0;
    reg [2:0] drop_pipeline = 0, corrupt_pipeline = 0;
    integer corrupt_remaining = 0;
    wire ack_busy = dut.link.ack_busy;
    wire ack_rising = ack_busy && !ack_busy_d;
    wire drop_request = ack_busy && drop_ack != 0 &&
                        ack_attempts + (ack_rising ? 1 : 0) == drop_ack;
    wire corrupt_request = corrupt_ack && ack_attempts == 1 &&
                           ack_byte_index == 9 && dut.link.ack_tx.data_symbol_start;
    wire dropping = drop_request || (|drop_pipeline);
    wire corrupting = corrupt_pipeline[2] || corrupt_remaining != 0;
    wire [63:0] reversed_phase = {-dac_data[63:48], -dac_data[47:32],
                                  -dac_data[31:16], -dac_data[15:0]};
    wire [63:0] channel_input = (!dac_valid || disconnected || dropping) ? 64'd0 :
                                (corrupting ? reversed_phase : dac_data);

    ofdm_rf_board #(.FRAME_BYTES(BYTES)) dut (
        .clk(clk), .aresetn(aresetn), .control(control), .status(status),
        .levels(levels), .retries(retries), .leds(leds),
        .adc_i(adc_i), .adc_q(adc_q), .adc_i_valid(1'b1), .adc_q_valid(1'b1),
        .adc_i_ready(adc_i_ready), .adc_q_ready(adc_q_ready),
        .dac_data(dac_data), .dac_valid(dac_valid), .dac_ready(1'b1),
        .src_clk(src_clk), .src_rst(src_rst), .src_en(src_en), .src_we(src_we),
        .src_addr(src_addr), .src_din(src_din), .src_dout(src_dout),
        .dst_clk(dst_clk), .dst_rst(dst_rst), .dst_en(dst_en), .dst_we(dst_we),
        .dst_addr(dst_addr), .dst_din(dst_din), .dst_dout(32'd0)
    );

    rf_iq_channel #(
        .DELAY_CLOCKS(DELAY_CLOCKS), .HALF_BEAT_DELAY(CHANNEL_PROFILE),
        .GAIN_I(CHANNEL_PROFILE ? 3 : -5), .GAIN_Q(CHANNEL_PROFILE ? -7 : 3),
        .ECHO_GAIN(CHANNEL_PROFILE)
    ) cable (
        .clk(clk), .dac_samples(channel_input), .adc_i(adc_i), .adc_q(adc_q)
    );

    always @(posedge src_clk) begin
        if (src_en) begin
            if (src_addr >= BYTES || src_addr[1:0] != 0 || src_we != 0)
                $fatal(1, "Invalid source BRAM request at %h", src_addr);
            src_dout <= {source[src_addr+3], source[src_addr+2],
                         source[src_addr+1], source[src_addr]};
        end
    end

    always @(posedge dst_clk) begin
        if (dst_en) begin
            if (sink_writes >= BYTES || dst_addr !== (sink_writes & ~3) ||
                dst_we !== (4'b0001 << (sink_writes % 4)))
                $fatal(1, "Unexpected destination write %0d to %h with mask %h",
                       sink_writes, dst_addr, dst_we);
            destination[sink_writes] <= dst_din[(sink_writes % 4)*8+:8];
            sink_writes <= sink_writes + 1;
        end
    end

    always @(posedge clk) begin
        cycles <= cycles + 1;
        if (aresetn) begin
            if ({dac_valid, adc_i_ready, adc_q_ready} !== 3'b111)
                $fatal(1, "RFDC streams were not continuous");
            if (status[31:25] !== STATUS_ID || status[5:4] !== 2'b00)
                $fatal(1, "Invalid board status or transport/address fault %h", status);
            if ($signed(dac_data[15:0]) > 0) iq_signs[0] <= 1;
            if ($signed(dac_data[15:0]) < 0) iq_signs[1] <= 1;
            if ($signed(dac_data[31:16]) > 0) iq_signs[2] <= 1;
            if ($signed(dac_data[31:16]) < 0) iq_signs[3] <= 1;

            ack_busy_d <= ack_busy;
            if (ack_rising)
                ack_attempts <= ack_attempts + 1;
            if (!ack_busy)
                ack_byte_index <= 0;
            else if (dut.link.ack_tx.data_symbol_start)
                ack_byte_index <= ack_byte_index + 1;
            drop_pipeline <= {drop_pipeline[1:0], drop_request};
            // Align a 180-degree phase hit to the DAC output of ACK CRC byte 9.
            // Busy and symbol start only schedule faults. RX sees only ADC samples.
            corrupt_pipeline <= {corrupt_pipeline[1:0], corrupt_request};
            if (corrupt_pipeline[2])
                corrupt_remaining <= SYMBOL_CLOCKS - 1;
            else if (corrupt_remaining != 0)
                corrupt_remaining <= corrupt_remaining - 1;
            if (dropping && dac_data != 0)
                dropped_beats <= dropped_beats + 1;
            if (corrupting && dac_data != 0)
                corrupted_beats <= corrupted_beats + 1;
        end
        if (cycles == (PACKETS + 12) * 175000 + 10000)
            $fatal(1, "RF simulation timed out, status=%h retries=%0d", status, retries);
    end

    integer index, dump, expected_retry;
    initial begin
        if (!$value$plusargs("DROP_ACK=%d", drop_ack)) drop_ack = 0;
        if (!$value$plusargs("CORRUPT_ACK=%d", corrupt_ack)) corrupt_ack = 0;
        if (!$value$plusargs("DISCONNECTED=%d", disconnected)) disconnected = 0;
        if (!$value$plusargs("OUTPUT=%s", output_path)) output_path = "received.raw";
        $readmemh("assets/image.mem", source);

        repeat (20) @(negedge clk);
        aresetn = 1;
        repeat (20) @(negedge clk);
        control[0] = 0;
        repeat (100) @(negedge clk);
        control[1] = 1;
        repeat (4) @(negedge clk);
        control[1] = 0;
        wait (status[0] || status[1]);
        repeat (10) @(negedge clk);
        if (iq_signs !== 4'b1111)
            $fatal(1, "The DAC did not carry signed I and Q");

        if (disconnected) begin
            if (status[3:0] !== 4'b0010 || status[24:6] !== 19'd0 ||
                sink_writes != 0 || ack_attempts != 0 || retries !== 32'd10)
                $fatal(1, "Disconnected channel did not fail safely: status=%h writes=%0d retries=%0d",
                       status, sink_writes, retries);
            $display("RF_SIM_PASS disconnected=1 sink_writes=0 retries=%0d cycles=%0d", retries, cycles);
        end else begin
            expected_retry = (drop_ack != 0 || corrupt_ack != 0) ? 1 : 0;
            if (status[3:0] !== 4'b1001 || status[24:6] != BYTES || sink_writes != BYTES)
                $fatal(1, "RF transfer failed: status=%h sink_writes=%0d", status, sink_writes);
            if (retries != expected_retry || ack_attempts != PACKETS + expected_retry ||
                dut.link.sender.timeouts != expected_retry ||
                dut.link.sender.acknowledged_packets != PACKETS ||
                dut.link.receiver.duplicates != expected_retry)
                $fatal(1, "Wrong retry, ACK, or duplicate count: retries=%0d ACK attempts=%0d",
                       retries, ack_attempts);
            if ((dropped_beats != 0) != (drop_ack != 0) ||
                (corrupted_beats != 0) != (corrupt_ack != 0) ||
                dut.link.sender.ack_crc_errors != corrupt_ack)
                $fatal(1, "Wrong RF fault or ACK CRC error count: drop=%0d corrupt=%0d ack_crc_errors=%0d",
                       dropped_beats, corrupted_beats, dut.link.sender.ack_crc_errors);

            dump = $fopen(output_path, "wb");
            if (!dump)
                $fatal(1, "Cannot write image dump %s", output_path);
            for (index = 0; index < BYTES; index = index + 1) begin
                if (destination[index] !== source[index])
                    $fatal(1, "Image byte %0d differs: %02h instead of %02h",
                           index, destination[index], source[index]);
                $fwrite(dump, "%c", destination[index]);
            end
            $fclose(dump);
            $display("RF_SIM_PASS packets=%0d bytes=%0d retries=%0d duplicates=%0d ack_crc_errors=%0d dropped_beats=%0d corrupted_beats=%0d cycles=%0d",
                     PACKETS, BYTES, retries, dut.link.receiver.duplicates,
                     dut.link.sender.ack_crc_errors, dropped_beats, corrupted_beats, cycles);
        end
        $finish;
    end
endmodule

// Packet BRAM IP has one synchronous read stage.
module ip_packet_ram (
    input wire clka, ena, wea, input wire [9:0] addra, input wire [7:0] dina,
    input wire clkb, enb, input wire [9:0] addrb, output reg [7:0] doutb
);
    reg [7:0] mem [0:1023];
    always @(posedge clka) if (ena && wea) mem[addra] <= dina;
    always @(posedge clkb) if (enb) doutb <= mem[addrb];
endmodule
