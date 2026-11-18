`timescale 1ns/1ps
// An independent real-valued DFT checks the occupied spectrum, modulation,
// cyclic prefix and padding of a TX-only burst.
module tb_ofdm_spectrum;
    reg clk = 0;
    always #5 clk = !clk;
    reg rst = 1;
    reg [7:0] s_data = 0;
    reg s_valid = 0, s_last = 0;
    wire s_ready, frame_done, underrun;
    wire signed [12:0] sample_i, sample_q;
    ofdm_tx dut (.bpsk(1'b0),.clk(clk),.rst(rst),.s_data(s_data),.s_valid(s_valid),
        .s_last(s_last),.s_ready(s_ready),.busy(),.sample_i(sample_i),.sample_q(sample_q),
        .frame_done(frame_done),.underrun(underrun));

    localparam [63:0] WORD0 = 64'hc38f59e12dab60f7;
    localparam [63:0] WORD1 = 64'hb17ec50000000000;
    localparam [31:0] TRAIN_SIGNS = 32'h69b8462d;
    integer wave_i [0:79], wave_q [0:79];
    integer checked = 0;

    task check_symbol(input integer symbol_number);
        integer n, k, tone, expect_i, expect_q, active_count;
        reg [63:0] data_word;
        real phase, real_bin, imag_bin;
        begin
            data_word = symbol_number == 2 ? WORD0 : WORD1;
            for (n=0; n<16; n=n+1)
                if (wave_i[n] != wave_i[n+64] || wave_q[n] != wave_q[n+64])
                    $fatal(1, "Wrong cyclic prefix sample %0d", n);
            active_count = 0;
            for (k=0; k<64; k=k+1) begin
                real_bin = 0; imag_bin = 0;
                for (n=0; n<64; n=n+1) begin
                    phase = 6.283185307179586 * k * n / 64.0;
                    real_bin = real_bin + wave_i[n+16]*$cos(phase) + wave_q[n+16]*$sin(phase);
                    imag_bin = imag_bin + wave_q[n+16]*$cos(phase) - wave_i[n+16]*$sin(phase);
                end
                expect_i = 0; expect_q = 0;
                if ((k >= 1 && k <= 16) || k >= 48) begin
                    tone = k < 32 ? k - 1 : k - 32;
                    expect_i = symbol_number < 2 ? (TRAIN_SIGNS[tone] ? -4096 : 4096) :
                               (data_word[63-2*tone] ? -4096 : 4096);
                    expect_q = symbol_number < 2 ? 0 : (data_word[62-2*tone] ? -4096 : 4096);
                end
                if (real_bin < expect_i-150 || real_bin > expect_i+150 ||
                    imag_bin < expect_q-150 || imag_bin > expect_q+150)
                    $fatal(1, "Symbol %0d bin %0d: got (%f,%f), expected (%0d,%0d)",
                           symbol_number,k,real_bin,imag_bin,expect_i,expect_q);
                if (real_bin < -2048 || real_bin > 2048) active_count = active_count + 1;
            end
            if (active_count != 32) $fatal(1, "Expected 32 tones, found %0d", active_count);
        end
    endtask

    integer position;
    always @(posedge clk) begin
        if (!rst && dut.sample_clock == 0 && (dut.state == dut.TRAIN || dut.state == dut.DATA)) begin
            position = dut.symbol_position;
            #1;
            wave_i[position] = sample_i;
            wave_q[position] = sample_q;
            if (position == 79) begin
                check_symbol(checked);
                checked = checked + 1;
            end
        end
    end

    task send_byte(input [7:0] value, input last_byte);
        begin
            @(negedge clk);
            while (!s_ready) @(negedge clk);
            s_data = value; s_last = last_byte; s_valid = 1;
            @(negedge clk);
            s_valid = 0; s_last = 0;
        end
    endtask

    integer index;
    initial begin
        repeat (4) @(negedge clk);
        rst = 0;
        for (index=0; index<8; index=index+1) send_byte(WORD0[63-8*index -: 8], 0);
        for (index=0; index<3; index=index+1) send_byte(WORD1[63-8*index -: 8], index == 2);
        wait (frame_done);
        if (underrun || checked != 4) $fatal(1, "TX burst did not finish with four symbols");
        $display("OFDM_SPECTRUM_PASS: 32 active bins, 16-sample CP, QPSK and final padding");
        $finish;
    end
    initial begin
        #50000;
        $fatal(1, "Timed out waiting for the TX burst");
    end
endmodule
