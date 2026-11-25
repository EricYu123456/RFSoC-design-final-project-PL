`timescale 1ns/1fs

module tb_rf_board;
    parameter integer PACKETS = 12, WINDOW=8;
    parameter integer CHANNEL_PROFILE = 0;
    // With CFO and sample-rate skew combined, reversing the sign exercises
    // both ends of the receiver's fixed per-burst sample window.
    parameter integer CLOCK_SKEW_PPM = 0;
    parameter integer CFO_HZ = 0;
    localparam integer BYTES = PACKETS * 1024;
    localparam integer DELAY_CLOCKS = CHANNEL_PROFILE ? 113 : 37;
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
    integer frame_id=0, cycles=0, polls=0, sacks=0, data_packets=0;
    reg lost_data=0, lost_ack=0;
    integer losses=0;
    always @(posedge clk_a) if(board_a.tx_done && board_a.link.kind==0) begin
        data_packets<=data_packets+1;
        if(board_a.link.number==2) lost_data<=1;
    end
    always @(posedge clk_a) if(board_a.tx_done && board_a.link.kind==3) polls<=polls+1;
    always @(posedge clk_b) if(board_b.tx_done && board_b.link.kind==4) begin
        sacks<=sacks+1; lost_ack<=1;
    end
    wire [63:0] data_input=(losses && !lost_data && board_a.link.kind==0 && board_a.link.number==2) ? 0 : dac_a;
    wire [63:0] ack_input=(losses && !lost_ack && board_b.link.kind==4) ? 0 : dac_b;
    // Superpose local TX leakage on the weak remote path. A short, decaying
    // tail and intermittent adjacent-channel energy persist after TX silence.
    reg signed [15:0] tail_a=0,tail_b=0;
    reg [7:0] phase_b=0;
    always @(posedge clk_a) tail_a<=!aresetn ? 0 : dac_a!=0 ? 16'sd13000 : tail_a-(tail_a>>>3)-(tail_a!=0);
    always @(posedge clk_b) begin
        tail_b<=!aresetn ? 0 : dac_b!=0 ? 16'sd13000 : tail_b-(tail_b>>>3)-(tail_b!=0);
        phase_b<=phase_b+1'b1;
    end
    function automatic [15:0] limited(input signed [15:0] remote,local_signal,tail,input signed [7:0] blocker);
        integer total;
        begin total=remote+4*local_signal+tail+blocker;
            limited=total>32767 ? 32767 : total< -32768 ? -32768 : total; end
    endfunction
    wire signed [7:0] interferer=CHANNEL_PROFILE && phase_b[7] ? (phase_b[2] ? 6 : -6) : 0;
    wire [31:0] input_a={limited(adc_i_a[31:16],dac_a[47:32],tail_a,0),limited(adc_i_a[15:0],dac_a[15:0],tail_a,0)};
    wire [31:0] input_b={limited(adc_i_b[31:16],dac_b[47:32],tail_b,interferer),limited(adc_i_b[15:0],dac_b[15:0],tail_b,interferer)};
    ofdm_rf_board #(.FRAME_BYTES(BYTES),.WINDOW(WINDOW)) board_a (
        .clk(clk_a), .aresetn(aresetn), .control(control_a), .status(status_a),
        .retries(retries), .adc_i(input_a), .adc_q(adc_q_a),
        .adc_i_valid(1'b1), .adc_q_valid(1'b1),
        .adc_i_ready(adc_i_ready_a), .adc_q_ready(adc_q_ready_a),
        .dac_data(dac_a), .dac_valid(dac_valid_a), .dac_ready(1'b1),
        .src_en(src_en), .src_we(src_we), .src_addr(src_addr), .src_dout(src_dout),
        .dst_en(forbidden_dst_en), .dst_dout(32'd0)
    );

    ofdm_rf_board #(.FRAME_BYTES(BYTES),.WINDOW(WINDOW)) board_b (
        .clk(clk_b), .aresetn(aresetn), .control(control_b), .status(status_b),
        .retries(duplicates), .adc_i(input_b), .adc_q(adc_q_b),
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
    ) forward_air (
        .tx_clk(clk_a), .rx_clk(clk_b), .dac_samples(data_input),
        .adc_i(adc_i_b), .adc_q(adc_q_b)
    );

    rf_iq_channel #(
        .DELAY_CLOCKS(51), .GAIN_I(5), .GAIN_Q(7), .CFO_HZ(-CFO_HZ)
    ) return_air (
        .tx_clk(clk_b), .rx_clk(clk_a), .dac_samples(ack_input),
        .adc_i(adc_i_a), .adc_q(adc_q_a)
    );

    always @(posedge clk_a) begin
        cycles<=cycles+1;
        if (src_en) case(src_addr)
            32'h7fff0: src_dout<=32'h9c7b1234;
            32'h7fff4: src_dout<={16'd2,frame_id[15:0]};
            default: if(src_addr<BYTES) src_dout<={source[src_addr+3],source[src_addr+2],source[src_addr+1],source[src_addr]};
        endcase
        if(aresetn && (status_a[5:4] || status_b[5:4] || status_a[1] || status_b[1]))
            $fatal(1,"modem fault %h %h",status_a,status_b);
        if(aresetn && dac_a!=0 && dac_b!=0) $fatal(1,"TDD collision");
        if(cycles>PACKETS*600000+4000000) $fatal(1,"timeout tx=%d kind=%d rx=%d",board_a.link.state,board_a.link.kind,board_b.link.state);
    end
    integer lane,addr;
    reg [31:0] meta0,meta1;
    always @(posedge clk_b) if(dst_en) begin
        if(dst_addr==32'h7fff0) meta0<=dst_din;
        else if(dst_addr==32'h7fff4) meta1<=dst_din;
        else for(lane=0;lane<4;lane=lane+1) if(dst_we[lane]) begin
            addr=dst_addr+lane;
            if(addr<BYTES) destination[addr]<=dst_din>>(8*lane);
            else if(addr<BYTES+PACKETS) quality[addr-BYTES]<=dst_din>>(8*lane);
            else $fatal(1,"sink overflow");
        end
    end
    integer j;
    initial begin
        losses=$test$plusargs("LOSS");
        $readmemh("image.mem",source);
        repeat(32) @(negedge clk_a); aresetn=1; control_b=32'h00804008; control_a=32'h00804000;
        repeat(1000) @(negedge clk_a);
        for(frame_id=0;frame_id<2;frame_id=frame_id+1) begin
            if(frame_id) $readmemh("image2.mem",source);
            for(j=0;j<BYTES;j=j+1) begin
                destination[j]=8'hxx;
            end
            for(j=0;j<PACKETS;j=j+1) quality[j]=0;
            control_a[1]=1; repeat(5) @(negedge clk_a);control_a[1]=0;
            repeat(20) @(negedge clk_a);
            wait(status_a[0] && status_b[0]);
            if(meta0!==32'h9c7b1234 || meta1!==131072+frame_id) $fatal(1,"stale metadata");
            for(j=0;j<BYTES;j=j+1) if(destination[j]!==source[j]) $fatal(1,"pixel mismatch %d",j);
            for(j=0;j<PACKETS;j=j+1) if(quality[j]!==2) $fatal(1,"bad packet %d",j);
            @(negedge clk_b);control_b[1]=1;repeat(5) @(negedge clk_b);control_b[1]=0;
            repeat(100) @(negedge clk_a);
        end
        if(PACKETS<=12 && (data_packets!=2*PACKETS+(losses ? 1 : 0) ||
           polls!=2*((PACKETS+WINDOW-1)/WINDOW)+(losses ? 2 : 0) || sacks!=polls))
            $fatal(1,"ARQ data=%d polls=%d sacks=%d",data_packets,polls,sacks);
        $display("AIR_PASS frames=2 packets=%0d cycles=%0d polls=%0d data=%0d",PACKETS,cycles,polls,data_packets);
        $finish;
    end
endmodule
