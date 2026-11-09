`timescale 1ns/1ps
module tb_image_link;
    `include "crc32_functions.vh"
    reg clk=0;
    always #5 clk=~clk;
    reg rst=1, start=0;
    string root_dir, output_dir, case_name;
    integer packets=300, expected_bytes, source_index=0, received_bytes=0;
    reg [15:0] packets_cfg;
    reg [7:0] source_image [0:307199];
    reg [7:0] received_image [0:307199];
    bit written [0:307199];
    reg [31:0] data_reference_crc [0:299];
    reg [31:0] ack_reference_crc [0:299];
    integer ack_counts [0:299];
    longint cycles=0, first_tx_cycle=-1, end_cycle;
    integer data_attempts=0, ack_attempts=0, mismatch_count=0;
    integer events_fd, raw_fd, result_fd, data_trace_fd, ack_trace_fd;
    integer data_trace_count=0, ack_trace_count=0;
    bit data_trace_active=0, ack_trace_active=0;
    bit data_trace_started=0, ack_trace_started=0;
    bit ack_drop_latched=0;
    reg [1:0] expected_data_bits=0, expected_ack_bits=0;
    integer forward_demod_errors=0, reverse_demod_errors=0;
    integer data_fault_packet, ack_corrupt_packet, ack_drop_packet;
    bit scenario_data, scenario_ack_corrupt, scenario_ack_drop, scenario_last_drop;
    bit scenario_wrong_ack, scenario_persistent, scenario_backpressure;

    wire [7:0] image_data = source_index < expected_bytes ? source_image[source_index] : 8'd0;
    wire image_valid = source_index < expected_bytes && (!scenario_backpressure || cycles%13!=0);
    wire image_ready;
    wire [7:0] sink_data;
    wire [18:0] sink_addr;
    wire sink_valid;
    wire sink_ready = !scenario_backpressure || cycles%32>=2;
    wire signed [15:0] data_wave, ack_wave;
    wire signed [15:0] reverse_channel = ack_drop_latched ? 16'sd0 : ack_wave;
    wire done, failed;

    // Corruption happens after the sender's CRC and before OOK modulation.
    wire [7:0] data_byte_xor =
        ((scenario_data && dut.sender.packet_id==data_fault_packet && dut.sender.retry_count==0) ||
         scenario_persistent) && dut.sender.byte_index==10 ? 8'h01 : 8'h00;
    wire [7:0] ack_byte_xor = scenario_ack_corrupt &&
        dut.receiver.current_packet==ack_corrupt_packet &&
        ack_counts[ack_corrupt_packet]==1 && dut.receiver.ack_byte_index==7 ? 8'h01 : 8'h00;
    wire ack_override_en = scenario_wrong_ack && dut.data_packet_id==0 && ack_counts[0]==0;

    image_link dut (.clk(clk), .rst(rst), .start(start), .frame_id(16'd0), .total_packets(packets_cfg),
        .image_data(image_data), .image_valid(image_valid), .image_ready(image_ready),
        .sink_data(sink_data), .sink_addr(sink_addr), .sink_valid(sink_valid), .sink_ready(sink_ready),
        .data_wave(data_wave), .data_channel(data_wave), .ack_wave(ack_wave), .ack_channel(reverse_channel),
        .data_byte_xor(data_byte_xor), .ack_byte_xor(ack_byte_xor),
        .ack_override_en(ack_override_en), .ack_override_id(16'd1), .done(done), .failed(failed));

    initial begin : setup
        integer unused, i;
        reg [31:0] standard_crc;
        standard_crc=32'hFFFFFFFF;
        for (i=0;i<9;i=i+1) standard_crc=crc32_update(standard_crc,8'h31+i);
        if (~standard_crc!==32'hCBF43926) $fatal(1,"CRC-32 standard vector failed");
        root_dir="."; output_dir="."; case_name="baseline";
        unused=$value$plusargs("ROOT=%s",root_dir);
        unused=$value$plusargs("OUT=%s",output_dir);
        unused=$value$plusargs("CASE=%s",case_name);
        unused=$value$plusargs("PACKETS=%d",packets);
        if (packets<1 || packets>300) $fatal(1,"PACKETS must be 1..300");
        if (case_name=="mixed" && packets<4) $fatal(1,"mixed requires at least 4 packets");
        if (!(case_name=="baseline" || case_name=="data_error" || case_name=="ack_corrupt" ||
              case_name=="ack_drop" || case_name=="last_ack_drop" || case_name=="wrong_ack" ||
              case_name=="persistent_error" || case_name=="backpressure" || case_name=="mixed"))
            $fatal(1,"Unknown test case: %s",case_name);
        packets_cfg=packets; expected_bytes=packets*1024;
        scenario_data=case_name=="data_error" || case_name=="mixed";
        scenario_ack_corrupt=case_name=="ack_corrupt" || case_name=="mixed";
        scenario_ack_drop=case_name=="ack_drop" || case_name=="mixed";
        scenario_last_drop=case_name=="last_ack_drop" || case_name=="mixed";
        scenario_wrong_ack=case_name=="wrong_ack";
        scenario_persistent=case_name=="persistent_error";
        scenario_backpressure=case_name=="backpressure";
        data_fault_packet=packets>10 ? 10 : 0;
        ack_corrupt_packet=packets>20 ? 20 : 1;
        ack_drop_packet=packets>30 ? 30 : (case_name=="mixed" ? 2 : 0);
        for (i=0;i<300;i=i+1) ack_counts[i]=0;
        for (i=0;i<307200;i=i+1) begin written[i]=0; received_image[i]=0; end
        $readmemh({root_dir,"/assets/image.mem"},source_image);
        $readmemh({root_dir,"/assets/data_crc.mem"},data_reference_crc);
        $readmemh({root_dir,"/assets/ack_crc.mem"},ack_reference_crc);
        events_fd=$fopen({output_dir,"/events.csv"},"w");
        raw_fd=$fopen({output_dir,"/received.raw"},"wb");
        data_trace_fd=$fopen({output_dir,"/data_wave.csv"},"w");
        ack_trace_fd=$fopen({output_dir,"/ack_wave.csv"},"w");
        if (!events_fd || !raw_fd || !data_trace_fd || !ack_trace_fd) $fatal(1,"Cannot open result files");
        $fdisplay(events_fd,"cycle,event,packet_id,retry");
        $fdisplay(data_trace_fd,"cycle,phase,tx_bit,sample,energy,rx_bit,rx_valid");
        $fdisplay(ack_trace_fd,"cycle,phase,tx_bit,sample,energy,rx_bit,rx_valid");
        $display("TEST_START case=%s packets=%0d",case_name,packets);
        repeat (16) @(negedge clk);
        rst=0;
        repeat (128) @(negedge clk);
        start=1;
        @(negedge clk); start=0;
    end

    always @(posedge clk) begin : monitor
        reg [7:0] expected_crc_byte;
        if (!rst) begin
            cycles<=cycles+1;
            expected_data_bits<={expected_data_bits[0],dut.data_phy.serial_bit};
            expected_ack_bits<={expected_ack_bits[0],ack_drop_latched ? 1'b0 : dut.ack_phy.serial_bit};
            if (cycles>128 && dut.data_bit_valid && dut.data_bit!==expected_data_bits[1])
                forward_demod_errors=forward_demod_errors+1;
            if (cycles>128 && dut.ack_bit_valid && dut.ack_bit!==expected_ack_bits[1])
                reverse_demod_errors=reverse_demod_errors+1;
            if (dut.data_busy && dut.ack_busy) $fatal(1,"Half-duplex violation");
            if (dut.data_underrun || dut.ack_underrun) $fatal(1,"OOK byte underrun");
            if (cycles > (packets+12)*150000) $fatal(1,"Global simulation timeout");
            if (image_valid && image_ready) source_index<=source_index+1;
            if (sink_valid && sink_ready) begin
                if (sink_addr>=expected_bytes) $fatal(1,"Out-of-range image write: %0d",sink_addr);
                if (written[sink_addr]) $fatal(1,"Duplicate image write: %0d",sink_addr);
                written[sink_addr]=1; received_image[sink_addr]=sink_data;
                if (sink_data!==source_image[sink_addr])
                    $fatal(1,"Pixel mismatch address=%0d expected=%02x received=%02x",sink_addr,source_image[sink_addr],sink_data);
                received_bytes=received_bytes+1;
                $fwrite(raw_fd,"%c",sink_data);
            end
            // Check RTL CRC bytes against Python/zlib before applying corruption.
            if (dut.data_valid && dut.data_ready && dut.sender.byte_index>=1034) begin
                expected_crc_byte=data_reference_crc[dut.sender.packet_id] >> ((1037-dut.sender.byte_index)*8);
                if (dut.data_byte!==expected_crc_byte) $fatal(1,"DATA CRC differs from Python/zlib");
            end
            if (dut.ack_valid && dut.ack_ready) begin
                if (dut.receiver.ack_byte_index==0) begin
                    ack_drop_latched <= ack_counts[dut.receiver.current_packet]==0 &&
                        ((scenario_ack_drop && dut.receiver.current_packet==ack_drop_packet) ||
                         (scenario_last_drop && dut.receiver.current_packet==packets-1));
                    ack_counts[dut.receiver.current_packet]=ack_counts[dut.receiver.current_packet]+1;
                end
                if (dut.receiver.ack_byte_index>=8) begin
                    expected_crc_byte=ack_reference_crc[dut.receiver.ack_packet_id] >> ((11-dut.receiver.ack_byte_index)*8);
                    if (dut.ack_byte!==expected_crc_byte) $fatal(1,"ACK CRC differs from Python/zlib");
                end
            end
            if (dut.data_start) begin
                data_attempts=data_attempts+1;
                if (first_tx_cycle<0) first_tx_cycle=cycles-1;
                $fdisplay(events_fd,"%0d,data_tx,%0d,%0d",cycles-1,dut.sender.packet_id,dut.sender.retry_count);
                if (!data_trace_started) begin data_trace_started=1; data_trace_active=1; end
            end
            if (dut.data_packet_done) begin
                if (dut.data_crc_ok) $fdisplay(events_fd,"%0d,data_crc_ok,%0d,%0d",cycles,dut.data_packet_id,dut.sender.retry_count);
                else $fdisplay(events_fd,"%0d,data_crc_bad,%0d,%0d",cycles,dut.data_packet_id,dut.sender.retry_count);
            end
            if (dut.receiver.commit_done) begin
                $fdisplay(events_fd,"%0d,commit,%0d,0",cycles,dut.receiver.committed_id);
                if (dut.receiver.committed_id%25==0 || dut.receiver.committed_id==packets-1)
                    $display("PROGRESS case=%s received=%0d/%0d cycles=%0d",case_name,dut.receiver.unique_packets,packets,cycles);
            end
            if (dut.ack_start) begin
                ack_attempts=ack_attempts+1;
                $fdisplay(events_fd,"%0d,ack_tx,%0d,%0d",cycles-1,dut.receiver.ack_packet_id,ack_drop_latched);
                if (!ack_trace_started) begin ack_trace_started=1; ack_trace_active=1; end
            end
            if (dut.ack_packet_done) begin
                if (dut.ack_crc_ok) $fdisplay(events_fd,"%0d,ack_crc_ok,%0d,0",cycles,dut.ack_packet_id);
                else $fdisplay(events_fd,"%0d,ack_crc_bad,%0d,0",cycles,dut.ack_packet_id);
            end
            if (dut.sender.state==5 && dut.sender.timer==4095)
                $fdisplay(events_fd,"%0d,timeout,%0d,%0d",cycles,dut.sender.packet_id,dut.sender.retry_count);
        end
    end

    always @(negedge clk) begin
        if (data_trace_active && data_trace_count<2048) begin
            $fdisplay(data_trace_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d",cycles,dut.sample_phase,
                dut.data_phy.serial_bit,$signed(data_wave),dut.data_energy,dut.data_bit,dut.data_bit_valid);
            data_trace_count=data_trace_count+1;
            if (data_trace_count==2048) data_trace_active=0;
        end
        if (ack_trace_active && ack_trace_count<2048) begin
            $fdisplay(ack_trace_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d",cycles,dut.sample_phase,
                dut.ack_phy.serial_bit,$signed(reverse_channel),dut.ack_energy,dut.ack_bit,dut.ack_bit_valid);
            ack_trace_count=ack_trace_count+1;
            if (ack_trace_count==2048) ack_trace_active=0;
        end
    end

    initial begin : completion
        integer i, expected_retries, expected_duplicates;
        wait (!rst);
        wait (done || failed);
        end_cycle=cycles;
        repeat (4) @(negedge clk);
        if (forward_demod_errors || reverse_demod_errors)
            $fatal(1,"OOK decisions differ from channel bits: forward=%0d reverse=%0d",forward_demod_errors,reverse_demod_errors);
        if (scenario_persistent) begin
            if (!failed || done || dut.sender.retransmissions!=10 || dut.receiver.crc_errors!=11 ||
                dut.sender.timeouts!=11 || received_bytes!=0 || data_attempts!=11)
                $fatal(1,"Retry-limit behavior incorrect");
        end else begin
            if (!done || failed || dut.receiver.unique_packets!=packets ||
                dut.sender.acknowledged_packets!=packets || received_bytes!=expected_bytes || source_index!=expected_bytes)
                $fatal(1,"Image transfer incomplete");
            for (i=0;i<expected_bytes;i=i+1)
                if (!written[i] || received_image[i]!==source_image[i]) mismatch_count=mismatch_count+1;
            if (mismatch_count!=0) $fatal(1,"Final byte comparison failed");
            expected_retries=(case_name=="mixed") ? 4 :
                (scenario_data || scenario_ack_corrupt || scenario_ack_drop || scenario_last_drop || scenario_wrong_ack ? 1 : 0);
            expected_duplicates=(case_name=="mixed") ? 3 :
                (scenario_ack_corrupt || scenario_ack_drop || scenario_last_drop || scenario_wrong_ack ? 1 : 0);
            if (dut.sender.retransmissions!=expected_retries || dut.sender.timeouts!=expected_retries ||
                dut.receiver.duplicates!=expected_duplicates || data_attempts!=packets+expected_retries)
                $fatal(1,"Unexpected ARQ counters: retries=%0d duplicates=%0d attempts=%0d",dut.sender.retransmissions,dut.receiver.duplicates,data_attempts);
            if (dut.receiver.crc_errors!=(scenario_data ? 1 : 0) ||
                dut.sender.ack_crc_errors!=(scenario_ack_corrupt ? 1 : 0) ||
                dut.sender.ignored_acks!=(scenario_wrong_ack ? 1 : 0) || dut.receiver.header_errors!=0)
                $fatal(1,"Unexpected CRC/header/ACK counters");
        end
        result_fd=$fopen({output_dir,"/result.json"},"w");
        $fdisplay(result_fd,"{");
        $fdisplay(result_fd,"  \"case\": \"%s\",",case_name);
        $fdisplay(result_fd,"  \"test_passed\": true,");
        $fdisplay(result_fd,"  \"link_success\": %s,",done ? "true" : "false");
        $fdisplay(result_fd,"  \"packets\": %0d,",packets);
        $fdisplay(result_fd,"  \"received_unique_packets\": %0d,",dut.receiver.unique_packets);
        $fdisplay(result_fd,"  \"received_bytes\": %0d,",received_bytes);
        $fdisplay(result_fd,"  \"mismatched_bytes\": %0d,",mismatch_count);
        $fdisplay(result_fd,"  \"data_attempts\": %0d,",data_attempts);
        $fdisplay(result_fd,"  \"ack_attempts\": %0d,",ack_attempts);
        $fdisplay(result_fd,"  \"data_crc_errors\": %0d,",dut.receiver.crc_errors);
        $fdisplay(result_fd,"  \"ack_crc_errors\": %0d,",dut.sender.ack_crc_errors);
        $fdisplay(result_fd,"  \"ignored_acks\": %0d,",dut.sender.ignored_acks);
        $fdisplay(result_fd,"  \"retransmissions\": %0d,",dut.sender.retransmissions);
        $fdisplay(result_fd,"  \"timeouts\": %0d,",dut.sender.timeouts);
        $fdisplay(result_fd,"  \"duplicate_packets\": %0d,",dut.receiver.duplicates);
        $fdisplay(result_fd,"  \"forward_demod_errors\": %0d,",forward_demod_errors);
        $fdisplay(result_fd,"  \"reverse_demod_errors\": %0d,",reverse_demod_errors);
        $fdisplay(result_fd,"  \"sample_clock_hz\": 100000000,");
        $fdisplay(result_fd,"  \"first_tx_cycle\": %0d,",first_tx_cycle);
        $fdisplay(result_fd,"  \"end_cycle\": %0d,",end_cycle);
        $fdisplay(result_fd,"  \"transfer_cycles\": %0d",end_cycle-first_tx_cycle);
        $fdisplay(result_fd,"}");
        $fclose(result_fd); $fclose(raw_fd); $fclose(events_fd);
        $fclose(data_trace_fd); $fclose(ack_trace_fd);
        $display("TEST_PASS case=%s packets=%0d retries=%0d bytes=%0d cycles=%0d",case_name,packets,dut.sender.retransmissions,received_bytes,end_cycle-first_tx_cycle);
        $finish;
    end
endmodule
