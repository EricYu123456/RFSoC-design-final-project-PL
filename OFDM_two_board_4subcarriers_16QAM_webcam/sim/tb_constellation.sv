`timescale 1ns/1ps
// Independent DFT of the serialized DAC-domain TX waveform for all 16 points on each tone.
module tb_constellation;
    reg clk = 0, rst = 1, valid = 0, last = 0;
    reg [7:0] data = 0;
    wire ready, done, underrun;
    wire signed [12:0] i_out, q_out;
    always #3 clk = ~clk;
    ofdm_tx dut(.clk(clk), .rst(rst), .s_data(data), .s_valid(valid), .s_ready(ready),
                .s_last(last), .sample_i(i_out), .sample_q(q_out), .busy(), .frame_done(done), .underrun(underrun));

    function automatic [15:0] payload(input integer n);
        reg [3:0] a, b, c, d;
        begin
            a = n; b = n+3; c = n+7; d = n+11;
            payload = {a, b, c, d};
        end
    endfunction

    function automatic integer level(input [1:0] pair);
        case (pair)
            0: level = 3; 1: level = 1; 3: level = -1; 2: level = -3;
        endcase
    endfunction

    initial begin : drive
        integer n;
        reg [15:0] word_in;
        repeat(6) @(negedge clk);
        rst = 0;
        for (n=0; n<32; n=n+1) begin
            while (!ready) @(negedge clk);
            word_in = payload(n / 2);
            data = n % 2 ? word_in[7:0] : word_in[15:8];
            valid = 1; last = n == 31;
            @(negedge clk);
        end
        valid = 0; last = 0;
    end

    initial begin : spectrum
        integer symbol, pos, bin, tone, n, t, expect_i, expect_q;
        integer xi [0:7], xq [0:7];
        reg [3:0] bits;
        real re, im, phase;
        wait(i_out == 2048 && q_out == 0);
        @(negedge clk);
        while (i_out == 2048 && q_out == 0) @(negedge clk);
        // We are on the first clock of CP sample 0. Sample at its center.
        repeat(3) @(negedge clk);
        n = 0;
        for (symbol=0; symbol<20; symbol=symbol+1) begin
            for (pos=0; pos<12; pos=pos+1) begin
                if (pos>=4) begin xi[pos-4] = i_out; xq[pos-4] = q_out; end
                repeat(8) @(negedge clk);
            end
            for (bin=0; bin<8; bin=bin+1) begin
                re=0; im=0;
                for (t=0; t<8; t=t+1) begin
                    phase=6.283185307179586*bin*t/8;
                    re=re + xi[t]*$cos(phase) + xq[t]*$sin(phase);
                    im=im + xq[t]*$cos(phase) - xi[t]*$sin(phase);
                end
                case(bin)
                    1: tone=0; 2: tone=1; 6: tone=2; 7: tone=3; default: tone=-1;
                endcase
                expect_i=0; expect_q=0;
                if (tone>=0) begin
                    if (symbol%5==0) expect_i=512;
                    else begin
                        bits=payload(n) >> (12-4*tone);
                        expect_i=256*level(bits[3:2]); expect_q=256*level(bits[1:0]);
                    end
                end
                // 181/256 approximates sqrt(1/2). Allow one DAC-domain LSB.
                if ($abs(re/8-expect_i)>1 || $abs(im/8-expect_q)>1)
                    $fatal(1,"symbol=%d bin=%d I/Q=(%f,%f) expected=(%d,%d)",
                           symbol,bin,re/8,im/8,expect_i,expect_q);
            end
            if (symbol%5!=0) n=n+1;
        end
        if (underrun) $fatal(1,"unexpected underrun");
        $display("CONSTELLATION_PASS 16 points on each of four active bins; four inactive bins at zero");
        $finish;
    end

    initial begin repeat(5000) @(negedge clk); $fatal(1,"timeout"); end
endmodule
