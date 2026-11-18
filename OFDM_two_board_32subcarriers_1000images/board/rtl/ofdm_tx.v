`timescale 1ns/1ps
// Pack eight MSB-first bytes into 32 QPSK tones. A partial last symbol
// has zero padding, which the receiver discards based on packet type.
module ofdm_tx (
    input wire clk, input wire rst, input wire bpsk,
    input wire [7:0] s_data, input wire s_valid, output wire s_ready,
    input wire s_last, output wire busy,
    output reg signed [12:0] sample_i, output reg signed [12:0] sample_q,
    output reg frame_done, output reg underrun
);
    `include "ofdm_phy.vh"
    localparam IDLE=0, MARKER=1, TRAIN=2, DATA=3;
    reg [1:0] state;
    reg first_training;
    reg [2:0] sample_clock;
    reg [6:0] symbol_position;
    reg [1:0] group_count;
    reg [63:0] pending_data, fft_data;
    reg [3:0] pending_count;
    reg pending_last, fft_last, current_last;
    reg fft_running, training_ready, wave_ready, playing_bank;
    wire packed_word = pending_count == (bpsk ? 4 : OFDM_BYTES) || pending_last;
    assign s_ready = !rst && !packed_word && !(state == DATA && current_last) &&
                     !(fft_running && fft_last) && !(wave_ready && fft_last);
    assign busy = state != IDLE || pending_count != 0;
    wire data_symbol_start = state == DATA && symbol_position == 0 && sample_clock == 0;

    // Cache training after reset. While a symbol is on air the same IFFT
    // prepares the next data symbol in the other waveform buffer.
    wire fft_start = !fft_running && (!training_ready || (!wave_ready && packed_word));
    reg [5:0] fft_bin;
    wire fft_in_ready, fft_out_valid;
    wire [5:0] fft_sample;
    wire signed [16:0] fft_i, fft_q;
    wire [4:0] tone = tone_number(fft_bin);
    wire i_negative = training_ready ? (bpsk ? fft_data[63-tone] : fft_data[63-2*tone]) : training_negative(tone);
    wire q_negative = bpsk ? i_negative : fft_data[62-2*tone];
    wire signed [15:0] bin_i = !active_bin(fft_bin) ? 16'sd0 :
                              i_negative ? -16'sd4096 : 16'sd4096;
    wire signed [15:0] bin_q = !active_bin(fft_bin) || !training_ready ? 16'sd0 :
                              q_negative ? -16'sd4096 : 16'sd4096;
    ofdm_fft64 #(.INVERSE(1)) ifft (.clk(clk),.rst(rst),.start(fft_start),
        .in_i(bin_i),.in_q(bin_q),.in_valid(1'b1),.in_ready(fft_in_ready),
        .out_i(fft_i),.out_q(fft_q),.out_valid(fft_out_valid),.out_index(fft_sample));

    reg [25:0] training_wave [0:63];
    reg [25:0] wave [0:127];
    // CP uses samples 48..63 before transmitting 0..63.
    wire [5:0] phase = symbol_position[5:0] - 6'd16;
    wire end_sample = sample_clock == OFDM_SAMPLE_CLOCKS-1;
    wire end_symbol = end_sample && symbol_position == OFDM_SAMPLES+OFDM_CP-1;
    wire need_data = end_symbol && (state == TRAIN ||
                     (state == DATA && !current_last && group_count < OFDM_DATA_PER_TRAIN));
    always @(posedge clk) begin
        if (!rst && fft_out_valid) begin
            if (!training_ready) training_wave[fft_sample] <= {fft_i[12:0],fft_q[12:0]};
            else wave[{!playing_bank,fft_sample}] <= {fft_i[12:0],fft_q[12:0]};
        end

        if (rst) begin
            state <= IDLE;
            sample_clock <= 0; symbol_position <= 0; group_count <= 0;
            pending_data <= 0; pending_count <= 0; pending_last <= 0;
            fft_data <= 0; fft_last <= 0; fft_bin <= 0; fft_running <= 0;
            training_ready <= 0; wave_ready <= 0; playing_bank <= 0;
            current_last <= 0; frame_done <= 0; underrun <= 0;
            sample_i <= 0; sample_q <= 0;
        end else begin
            frame_done <= 0;
            case (state)
                MARKER: {sample_i,sample_q} <= {13'sd2048,13'sd0};
                TRAIN: {sample_i,sample_q} <= training_wave[phase];
                DATA: {sample_i,sample_q} <= wave[{playing_bank,phase}];
                default: {sample_i,sample_q} <= 0;
            endcase

            if (s_valid && s_ready) begin
                pending_data[63-8*pending_count -: 8] <= s_data;
                pending_count <= pending_count + 1'b1;
                pending_last <= s_last;
            end
            if (fft_start) begin
                fft_running <= 1;
                fft_bin <= 0;
                if (training_ready) begin
                    fft_data <= pending_data;
                    fft_last <= pending_last;
                    pending_data <= 0; pending_count <= 0; pending_last <= 0;
                end
            end
            if (fft_in_ready) fft_bin <= fft_bin + 1'b1;
            if (fft_out_valid && fft_sample == OFDM_SAMPLES-1) begin
                fft_running <= 0;
                if (!training_ready) training_ready <= 1;
                else wave_ready <= 1;
            end

            if (state == IDLE) begin
                if (training_ready && pending_count != 0) begin state <= MARKER; first_training<=1; end
                sample_clock <= 0;
                symbol_position <= 0;
            end else begin
                sample_clock <= end_sample ? 3'd0 : sample_clock + 1'b1;
                if (end_sample) symbol_position <= symbol_position + 1'b1;
                if (state == MARKER && end_sample && symbol_position == OFDM_MARKER-1) begin
                    state <= TRAIN;
                    symbol_position <= 0;
                    group_count <= 0;
                end else if (end_symbol) begin
                    symbol_position <= 0;
                    if (state == TRAIN && first_training) begin
                        first_training<=0;
                    end else if (state == DATA && current_last) begin
                        state <= IDLE;
                        frame_done <= 1;
                    end else if (need_data && wave_ready) begin
                        state <= DATA;
                        group_count <= group_count + 1'b1;
                        playing_bank <= !playing_bank;
                        wave_ready <= 0;
                        current_last <= fft_last;
                    end else if (need_data) begin
                        state <= IDLE;
                        underrun <= 1;
                    end else begin
                        state <= TRAIN;
                        group_count <= 0;
                    end
                end
            end
        end
    end
endmodule
