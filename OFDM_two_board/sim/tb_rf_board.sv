`timescale 1ns/1fs

module tb_rf_board;
    parameter integer PACKETS = 3;
    parameter integer CHANNEL_PROFILE = 0;
    // With CFO and sample-rate skew combined, reversing the sign exercises
    // both ends of the receiver's fixed per-burst sample window.
    parameter integer CLOCK_SKEW_PPM = 0;
    parameter integer CFO_HZ = 0;
    localparam integer BYTES = PACKETS * 1024;
    localparam integer SYMBOL_CLOCKS = 12 * 8;
    localparam integer DELAY_CLOCKS = CHANNEL_PROFILE ? 113 : 37;
    localparam [6:0] STATUS_ID = 7'h3f;
    localparam [31:0] RESET_CONTROL = 32'h04004001;
    localparam integer CLOCK_A_HALF_FS = 3255208;
    localparam integer CLOCK_B_HALF_FS = CLOCK_A_HALF_FS
        - $rtoi(3.255208 * CLOCK_SKEW_PPM);

    reg clk_a = 0, clk_b = 0;
    always #(CLOCK_A_HALF_FS * 1fs) clk_a = ~clk_a;
    initial begin
        #1.125;
        forever #(CLOCK_B_HALF_FS * 1fs) clk_b = ~clk_b;
    end

    reg aresetn = 0;
    reg [31:0] control_a = RESET_CONTROL, control_b = RESET_CONTROL | 8;
    wire [31:0] status_a, status_b, retries, duplicates;
    wire [31:0] adc_i_a, adc_q_a, adc_i_b, adc_q_b;
    wire [63:0] dac_a, dac_b;
    wire dac_valid_a, dac_valid_b;
    wire adc_i_ready_a, adc_q_ready_a, adc_i_ready_b, adc_q_ready_b;
    wire src_en, dst_en, forbidden_src_en, forbidden_dst_en;
    wire [3:0] src_we, dst_we;
    wire [31:0] src_addr, dst_addr, dst_din;
    reg [31:0] src_dout = 0;
    reg [7:0] source [0:307199];
    reg [7:0] destination [0:BYTES-1];
    reg [7:0] quality [0:PACKETS-1];
    integer payload_writes [0:PACKETS-1];
    integer quality_writes [0:PACKETS-1];
    integer attempts [0:PACKETS-1];

    localparam integer CORRUPT_ALWAYS = 1, CORRUPT_ONCE = 2, DROP_DATA = 3, CORRUPT_HEADER = 4;
    localparam integer FAULT_PAYLOAD_INDEX = 86;
    integer data_fault = 0, fault_packet = 1, block_ack_packet = -1;
    integer drop_ack = 0;
    integer corrupt_ack = 0;
    integer disconnected = 0;
    string output_path;
    integer cycles = 0;
    integer ack_attempts = 0, ack_byte_index = 0;
    integer dropped_beats = 0, corrupted_beats = 0;
    reg [3:0] iq_signs = 0;
    reg ack_busy_d = 0;
    reg [2:0] drop_pipeline = 0, corrupt_pipeline = 0;
    integer corrupt_remaining = 0;
    wire ack_busy = board_b.link.tx_busy;
    wire ack_rising = ack_busy && !ack_busy_d;
    wire drop_request = ack_busy && drop_ack != 0 &&
                        ack_attempts + (ack_rising ? 1 : 0) == drop_ack;
    wire corrupt_request = corrupt_ack && ack_attempts == 1 &&
                           ack_byte_index == 9 && board_b.link.tx.data_symbol_start;
    wire blocked_ack = block_ack_packet >= 0 && ack_busy &&
                       board_b.link.receiver.ack_packet_id == block_ack_packet;
    wire dropping = drop_request || blocked_ack || (|drop_pipeline);
    wire corrupting = corrupt_pipeline[2] || corrupt_remaining != 0;
    wire [63:0] reversed_phase = {-dac_b[63:48], -dac_b[47:32],
                                  -dac_b[31:16], -dac_b[15:0]};
    wire data_busy = board_a.link.tx_busy;
    wire [15:0] packet_id = board_a.link.sender.packet_id;
    reg data_busy_d = 0;
    integer data_byte_index = 0;
    reg [2:0] data_drop_pipeline = 0, data_corrupt_pipeline = 0;
    integer data_corrupt_remaining = 0;
    wire drop_data = data_fault == DROP_DATA && data_busy && packet_id == fault_packet;
    wire damage_data = data_busy && packet_id == fault_packet && board_a.link.tx.data_symbol_start &&
                       ((data_fault == CORRUPT_HEADER && data_byte_index == 10) ||
                        ((data_fault == CORRUPT_ALWAYS ||
                          (data_fault == CORRUPT_ONCE && attempts[fault_packet] == 1)) &&
                         data_byte_index == 14 + FAULT_PAYLOAD_INDEX));
    wire [63:0] data_phase_hit = {-dac_a[63:48], -dac_a[47:32],
                                  -dac_a[31:16], -dac_a[15:0]};
    wire [63:0] data_input = (!dac_valid_a || disconnected || drop_data || (|data_drop_pipeline)) ?
                             64'd0 : (data_corrupt_pipeline[2] || data_corrupt_remaining != 0) ?
                             data_phase_hit : dac_a;
    wire [63:0] ack_input = (!dac_valid_b || disconnected || dropping) ? 64'd0 :
                            (corrupting ? reversed_phase : dac_b);

    ofdm_rf_board #(.FRAME_BYTES(BYTES)) board_a (
        .clk(clk_a), .aresetn(aresetn), .control(control_a), .status(status_a),
        .retries(retries), .adc_i(adc_i_a), .adc_q(adc_q_a),
        .adc_i_valid(1'b1), .adc_q_valid(1'b1),
        .adc_i_ready(adc_i_ready_a), .adc_q_ready(adc_q_ready_a),
        .dac_data(dac_a), .dac_valid(dac_valid_a), .dac_ready(1'b1),
        .src_en(src_en), .src_we(src_we), .src_addr(src_addr), .src_dout(src_dout),
        .dst_en(forbidden_dst_en), .dst_dout(32'd0)
    );

    ofdm_rf_board #(.FRAME_BYTES(BYTES)) board_b (
        .clk(clk_b), .aresetn(aresetn), .control(control_b), .status(status_b),
        .retries(duplicates), .adc_i(adc_i_b), .adc_q(adc_q_b),
        .adc_i_valid(1'b1), .adc_q_valid(1'b1),
        .adc_i_ready(adc_i_ready_b), .adc_q_ready(adc_q_ready_b),
        .dac_data(dac_b), .dac_valid(dac_valid_b), .dac_ready(1'b1),
        .src_en(forbidden_src_en), .src_dout(32'd0),
        .dst_en(dst_en), .dst_we(dst_we), .dst_addr(dst_addr),
        .dst_din(dst_din), .dst_dout(32'd0)
    );

    rf_iq_channel #(
        .DELAY_CLOCKS(DELAY_CLOCKS), .HALF_BEAT_DELAY(CHANNEL_PROFILE),
        .GAIN_I(CHANNEL_PROFILE ? 3 : -5), .GAIN_Q(CHANNEL_PROFILE ? -7 : 3),
        .ECHO_GAIN(CHANNEL_PROFILE), .CFO_HZ(CFO_HZ)
    ) data_cable (
        .tx_clk(clk_a), .rx_clk(clk_b), .dac_samples(data_input),
        .adc_i(adc_i_b), .adc_q(adc_q_b)
    );

    rf_iq_channel #(
        .DELAY_CLOCKS(51), .GAIN_I(5), .GAIN_Q(7), .CFO_HZ(-CFO_HZ)
    ) ack_cable (
        .tx_clk(clk_b), .rx_clk(clk_a), .dac_samples(ack_input),
        .adc_i(adc_i_a), .adc_q(adc_q_a)
    );

    always @(posedge clk_a) begin
        if (src_en) begin
            if (src_addr >= BYTES || src_addr[1:0] != 0 || src_we != 0)
                $fatal(1, "Invalid source BRAM request at %h", src_addr);
            src_dout <= {source[src_addr+3], source[src_addr+2],
                         source[src_addr+1], source[src_addr]};
        end
    end

    integer lane, write_offset, write_packet;
    always @(posedge clk_b) begin
        if (dst_en) begin
            case (dst_we)
                4'b0001: lane = 0;
                4'b0010: lane = 1;
                4'b0100: lane = 2;
                4'b1000: lane = 3;
                default: $fatal(1, "Invalid destination byte mask %h", dst_we);
            endcase
            write_offset = dst_addr + lane;
            if (dst_addr[1:0] != 0 || write_offset >= BYTES + PACKETS)
                $fatal(1, "Invalid destination write to %h", write_offset);
            if (write_offset < BYTES) begin
                write_packet = write_offset / 1024;
                if (write_offset % 1024 != payload_writes[write_packet] % 1024)
                    $fatal(1, "Invalid payload write ordering for packet %0d at %h",
                           write_packet, write_offset);
                destination[write_offset] <= dst_din[lane*8+:8];
                payload_writes[write_packet] <= payload_writes[write_packet] + 1;
            end else begin
                write_packet = write_offset - BYTES;
                if (payload_writes[write_packet] != (quality_writes[write_packet] + 1) * 1024)
                    $fatal(1, "Packet %0d quality written before the full payload", write_packet);
                quality[write_packet] <= dst_din[lane*8+:8];
                quality_writes[write_packet] <= quality_writes[write_packet] + 1;
            end
        end
    end

    always @(posedge clk_a) begin
        cycles <= cycles + 1;
        if (aresetn) begin
            if ({dac_valid_a, adc_i_ready_a, adc_q_ready_a} !== 3'b111 ||
                status_a[31:25] !== STATUS_ID || status_a[5:4] !== 2'b00 ||
                forbidden_dst_en)
                $fatal(1, "Invalid sender status or stream %h", status_a);
            if ($signed(dac_a[15:0]) > 0) iq_signs[0] <= 1;
            if ($signed(dac_a[15:0]) < 0) iq_signs[1] <= 1;
            if ($signed(dac_a[31:16]) > 0) iq_signs[2] <= 1;
            if ($signed(dac_a[31:16]) < 0) iq_signs[3] <= 1;

            data_busy_d <= data_busy;
            if (data_busy && !data_busy_d)
                attempts[packet_id] <= attempts[packet_id] + 1;
            if (!data_busy)
                data_byte_index <= 0;
            else if (board_a.link.tx.data_symbol_start)
                data_byte_index <= data_byte_index + 1;
            data_drop_pipeline <= {data_drop_pipeline[1:0], drop_data};
            data_corrupt_pipeline <= {data_corrupt_pipeline[1:0], damage_data};
            if (data_corrupt_pipeline[2])
                data_corrupt_remaining <= SYMBOL_CLOCKS - 1;
            else if (data_corrupt_remaining != 0)
                data_corrupt_remaining <= data_corrupt_remaining - 1;
        end
        if (cycles == (disconnected ? PACKETS * 11 : PACKETS + 13) * 190000 + 10000)
            $fatal(1, "Timed out: TX=%h RX=%h retries=%0d ACK attempts=%0d",
                   status_a, status_b, retries, ack_attempts);
    end

    always @(posedge clk_b) begin
        if (aresetn) begin
            if ({dac_valid_b, adc_i_ready_b, adc_q_ready_b} !== 3'b111 ||
                status_b[31:25] !== STATUS_ID || status_b[5:4] !== 2'b00 || forbidden_src_en)
                $fatal(1, "Invalid receiver status or stream %h", status_b);
            ack_busy_d <= ack_busy;
            if (ack_rising)
                ack_attempts <= ack_attempts + 1;
            if (!ack_busy)
                ack_byte_index <= 0;
            else if (board_b.link.tx.data_symbol_start)
                ack_byte_index <= ack_byte_index + 1;
            drop_pipeline <= {drop_pipeline[1:0], drop_request || blocked_ack};
            // Place a phase hit on one ACK CRC byte after DAC pipelining.
            corrupt_pipeline <= {corrupt_pipeline[1:0], corrupt_request};
            if (corrupt_pipeline[2])
                corrupt_remaining <= SYMBOL_CLOCKS - 1;
            else if (corrupt_remaining != 0)
                corrupt_remaining <= corrupt_remaining - 1;
            if (dropping && dac_b != 0)
                dropped_beats <= dropped_beats + 1;
            if (corrupting && dac_b != 0)
                corrupted_beats <= corrupted_beats + 1;
        end
    end

    integer index, packet, dump;
    integer expected_quality, expected_writes, expected_attempts;
    integer expected_retry, expected_exhausted, expected_duplicates, expected_present, expected_acks;
    integer crc_bad_packets = 0, missing_packets = 0, mismatched_bytes = 0;
    reg [7:0] expected_byte;
    initial begin
        if (!$value$plusargs("DROP_ACK=%d", drop_ack)) drop_ack = 0;
        if (!$value$plusargs("CORRUPT_ACK=%d", corrupt_ack)) corrupt_ack = 0;
        if (!$value$plusargs("DISCONNECTED=%d", disconnected)) disconnected = 0;
        if (!$value$plusargs("OUTPUT=%s", output_path)) output_path = "received.raw";
        if (!$value$plusargs("DATA_FAULT=%d", data_fault)) data_fault = 0;
        if (!$value$plusargs("FAULT_PACKET=%d", fault_packet)) fault_packet = 1;
        if (!$value$plusargs("BLOCK_ACK_PACKET=%d", block_ack_packet)) block_ack_packet = -1;
        $readmemh("assets/image.mem", source);
        for (index = 0; index < BYTES; index = index + 1)
            destination[index] = 0;
        for (index = 0; index < PACKETS; index = index + 1) begin
            quality[index] = 0;
            payload_writes[index] = 0;
            quality_writes[index] = 0;
            attempts[index] = 0;
        end

        repeat (20) @(negedge clk_a);
        aresetn = 1;
        repeat (20) @(negedge clk_b);
        control_b[0] = 0;
        // Starting B in receiver mode must never send image data.
        control_b[1] = 1;
        repeat (4) @(negedge clk_b);
        control_b[1] = 0;
        repeat (100) @(negedge clk_a);
        if (status_b[3:0] !== 4'b0100 || dac_a != 0 || dac_b != 0)
            $fatal(1, "Receiver failed to arm quietly before sender: %h", status_b);
        control_a[0] = 0;
        repeat (100) @(negedge clk_a);
        control_a[1] = 1;
        repeat (4) @(negedge clk_a);
        control_a[1] = 0;
        wait (status_a[0] || status_a[1]);
        repeat (10) @(negedge clk_a);
        if (iq_signs !== 4'b1111)
            $fatal(1, "The data DAC did not carry signed I and Q");

        expected_retry = (drop_ack != 0 || corrupt_ack != 0) ? 1 : 0;
        expected_duplicates = expected_retry;
        expected_exhausted = 0;
        expected_present = PACKETS;
        if (disconnected) begin
            expected_exhausted = PACKETS;
            expected_retry = PACKETS * 10;
            expected_present = 0;
        end else if (data_fault == CORRUPT_ONCE) begin
            expected_retry = 1;
        end else if (data_fault != 0 || block_ack_packet >= 0) begin
            expected_exhausted = 1;
            expected_retry = 10;
            if (data_fault == DROP_DATA || data_fault == CORRUPT_HEADER)
                expected_present = PACKETS - 1;
            if (block_ack_packet >= 0)
                expected_duplicates = 10;
        end
        expected_acks = expected_present - (data_fault == CORRUPT_ALWAYS ? 1 : 0) + expected_duplicates;

        if (status_a[3:0] !== 4'b0001 || status_a[24:6] != expected_exhausted ||
            status_b[3:0] !== (expected_present == PACKETS ? 4'b1101 : 4'b0100) ||
            status_b[24:6] != expected_present * 1024 || board_a.source_index != BYTES)
            $fatal(1, "Unexpected transfer completion: TX=%h RX=%h bytes read=%0d",
                   status_a, status_b, board_a.source_index);
        if (retries != expected_retry || ack_attempts != expected_acks ||
            board_a.link.sender.timeouts != expected_retry + expected_exhausted ||
            board_a.link.sender.acknowledged_packets != PACKETS - expected_exhausted ||
            duplicates != expected_duplicates)
            $fatal(1, "Unexpected counters: retries=%0d ACK attempts=%0d duplicates=%0d",
                   retries, ack_attempts, duplicates);
        if ((dropped_beats != 0) != (drop_ack != 0 || block_ack_packet >= 0) ||
            (corrupted_beats != 0) != (corrupt_ack != 0) ||
            board_a.link.sender.ack_crc_errors != corrupt_ack)
            $fatal(1, "Wrong ACK fault count: drop=%0d corrupt=%0d CRC errors=%0d",
                   dropped_beats, corrupted_beats, board_a.link.sender.ack_crc_errors);

        for (packet = 0; packet < PACKETS; packet = packet + 1) begin
            expected_quality = 2;
            expected_attempts = 1;
            expected_writes = 1;
            if (drop_ack == packet + 1 || (corrupt_ack != 0 && packet == 0))
                expected_attempts = 2;
            if (packet == block_ack_packet)
                expected_attempts = 11;
            if (disconnected || (packet == fault_packet && data_fault != 0)) begin
                expected_attempts = data_fault == CORRUPT_ONCE ? 2 : 11;
                if (disconnected || data_fault == DROP_DATA || data_fault == CORRUPT_HEADER) begin
                    expected_quality = 0;
                    expected_writes = 0;
                end else if (data_fault == CORRUPT_ALWAYS) begin
                    expected_quality = 1;
                    expected_writes = 11;
                end else expected_writes = 2;
            end
            if (quality[packet] != expected_quality || attempts[packet] != expected_attempts ||
                payload_writes[packet] != expected_writes * 1024 || quality_writes[packet] != expected_writes)
                $fatal(1, "Packet %0d: quality %0d/%0d, tries %0d/%0d, bytes written %0d, metadata writes %0d",
                       packet, quality[packet], expected_quality, attempts[packet], expected_attempts,
                       payload_writes[packet], quality_writes[packet]);
            if (quality[packet] == 1) crc_bad_packets = crc_bad_packets + 1;
            if (quality[packet] == 0) missing_packets = missing_packets + 1;
        end

        dump = $fopen(output_path, "wb");
        if (!dump)
            $fatal(1, "Cannot write image dump %s", output_path);
        for (index = 0; index < BYTES; index = index + 1) begin
            expected_byte = quality[index/1024] == 0 ? 8'd0 : source[index];
            if (quality[index/1024] == 1 && index % 1024 == FAULT_PAYLOAD_INDEX)
                expected_byte = ~expected_byte;
            if (destination[index] !== expected_byte)
                $fatal(1, "Image byte %0d differs: %02h instead of %02h",
                       index, destination[index], expected_byte);
            if (destination[index] != source[index]) mismatched_bytes = mismatched_bytes + 1;
            $fwrite(dump, "%c", destination[index]);
        end
        $fclose(dump);
        $display("RF_SIM_PASS packets=%0d bytes=%0d retries=%0d exhausted=%0d duplicates=%0d ack_crc_errors=%0d crc_bad_packets=%0d missing_packets=%0d mismatched_bytes=%0d dropped_beats=%0d corrupted_beats=%0d cycles=%0d",
                 PACKETS, BYTES, retries, expected_exhausted, duplicates, board_a.link.sender.ack_crc_errors,
                 crc_bad_packets, missing_packets, mismatched_bytes, dropped_beats, corrupted_beats, cycles);
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
