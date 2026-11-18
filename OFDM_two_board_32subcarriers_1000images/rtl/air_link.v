`timescale 1ns/1ps
module air_link #(
    parameter PACKETS=300, parameter GUARD=2304, parameter ACK_TIMEOUT=65536,
    parameter OPEN_TIMEOUT=3072000, parameter OPEN_ATTEMPTS=1500, parameter WINDOW=8
)(
    input wire clk, rst, receiver, start,
    input wire [31:0] local_session, input wire [15:0] local_frame, local_count,
    output wire [18:0] source_address, input wire [7:0] source_byte,
    output wire write_enable, output wire [18:0] write_address, output wire [7:0] write_data,
    output wire [7:0] tx_byte, output wire tx_valid, tx_last,
    input wire tx_ready, tx_done, rx_start, rx_valid, input wire [7:0] rx_byte,
    output reg done, failed, output wire busy, output reg [31:0] retries,
    output reg [15:0] missing, output reg [31:0] session,
    output reg [15:0] frame, count
);
    `include "air_protocol.vh"
    localparam IDLE=0, GAP=1, LAUNCH=2, ON_AIR=3, LISTEN=4, PICK=5, ANSWER=6;
    localparam PAD=((PACKETS+7)/8)*8;
    reg [2:0] state;
    reg [31:0] timer;
    reg [10:0] attempts;
    reg [15:0] base, number;
    reg [7:0] kind, mask, pending;
    reg [31:0] reply_session;
    reg [15:0] reply_frame;
    wire [7:0] rx_kind, rx_mask;
    wire [31:0] rx_session;
    wire [15:0] rx_frame, rx_number;
    wire received, payload_good;
    wire match_id=session==rx_session && frame==rx_frame;
    reg bound, opened;
    reg [PAD-1:0] verified;
    wire [PAD+6:0] ack_bits={7'b0,verified};
    reg [15:0] valid_count;
    reg [10:0] copy_index;
    reg copying, copy_good;
    reg [8:0] copy_packet;
    wire [7:0] scratch_data;
    wire [9:0] scratch_addr=copy_index[9:0];
    assign write_enable=copying && copy_index!=0;
    wire [9:0] write_offset=copy_index[9:0]-1'b1;
    assign write_address=copy_index==1025 ? PACKETS*1024+copy_packet : {copy_packet,write_offset};
    assign write_data=copy_index==1025 ? (copy_good ? 8'd2 : 8'd1) : scratch_data;
    assign busy=receiver || state!=IDLE;
    wire [15:0] remaining=PACKETS-base;
    wire [7:0] required_mask=(remaining>=WINDOW ? (1<<WINDOW)-1 : (1<<remaining[2:0])-1);
    wire [7:0] absent=required_mask & ~rx_mask;
    function [2:0] lowest(input [7:0] bits);
        integer i;
        begin lowest=0; for(i=7;i>=0;i=i-1) if(bits[i]) lowest=i; end
    endfunction
    function [3:0] popcount(input [7:0] bits);
        integer i;
        begin popcount=0; for(i=0;i<8;i=i+1) popcount=popcount+bits[i]; end
    endfunction
    wire [2:0] slot=lowest(pending);
    air_codec codec(.clk(clk),.rst(rst),.launch(state==LAUNCH),
        .kind(kind),.mask(mask),.session(receiver ? reply_session : session),
        .frame(receiver ? reply_frame : frame),.number(number),
        .source_address(source_address),.source_byte(source_byte),
        .tx_byte(tx_byte),.tx_valid(tx_valid),.tx_last(tx_last),.tx_ready(tx_ready),
        .rx_start(rx_start),.rx_valid(rx_valid),.rx_byte(rx_byte),
        .received(received),.payload_good(payload_good),.rx_kind(rx_kind),
        .rx_mask(rx_mask),.rx_session(rx_session),.rx_frame(rx_frame),.rx_number(rx_number),
        .scratch_addr(scratch_addr),.scratch_data(scratch_data));
    task schedule(input [7:0] message, input [15:0] index, input [7:0] bitmap);
        begin kind<=message; number<=index; mask<=bitmap; state<=GAP; timer<=0; end
    endtask
    task advance_window;
        begin
            attempts<=0;
            if (base+WINDOW>=PACKETS) schedule(FIN,0,0);
            else begin base<=base+WINDOW; state<=ANSWER; end
        end
    endtask
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; done<=0; failed<=0; timer<=0; attempts<=0; retries<=0;
            missing<=0; session<=0; frame<=0; count<=0; bound<=0; opened<=0;
            verified<=0; valid_count<=0; copying<=0; copy_index<=0;
            base<=0; pending<=0; number<=0; kind<=0; mask<=0;
        end else begin
            if (copying) begin
                copy_index<=copy_index+1'b1;
                if (copy_index==1025) begin
                    copying<=0;
                    if (copy_good) begin verified[copy_packet]<=1; valid_count<=valid_count+1'b1; end
                end
            end
            case (state)
                IDLE: if (!receiver && start) begin
                    session<=local_session; frame<=local_frame; count<=local_count;
                    done<=0; failed<=0; retries<=0; attempts<=0; missing<=0; base<=0;
                    schedule(OPEN,local_count,0);
                end
                GAP: if (timer==GUARD-1) state<=LAUNCH; else timer<=timer+1'b1;
                LAUNCH: state<=ON_AIR;
                ON_AIR: if(tx_done) begin
                    timer<=0;
                    if(receiver) state<=IDLE;
                    else state<=kind==DATA ? PICK : LISTEN;
                end
                ANSWER: begin pending<=required_mask; state<=PICK; end
                PICK: if (pending==0) schedule(POLL,base,0);
                      else begin pending[slot]<=0; schedule(DATA,base+slot,0); end
                LISTEN: begin
                    timer<=timer+1'b1;
                    if(received && match_id && kind==OPEN && rx_kind==READY && rx_number==count) begin
                        attempts<=0; state<=ANSWER;
                    end else if(received && match_id && kind==POLL && rx_kind==SACK && rx_number==base) begin
                        if(absent==0) advance_window();
                        else if(attempts>=10) begin missing<=missing+popcount(absent); advance_window(); end
                        else begin pending<=absent; attempts<=attempts+1'b1;
                            retries<=retries+popcount(absent); state<=PICK; end
                    end else if(received && match_id && kind==FIN && rx_kind==CLOSED) begin
                        done<=1; state<=IDLE;
                    end else if(timer==(kind==OPEN ? OPEN_TIMEOUT-1 : ACK_TIMEOUT-1)) begin
                        if (attempts >= (kind==OPEN ? OPEN_ATTEMPTS-1 : 10)) begin
                            if(kind==POLL) begin missing<=missing+popcount(required_mask); advance_window(); end
                            else begin failed<=1; state<=IDLE; end
                        end else begin attempts<=attempts+1'b1; retries<=retries+1'b1; schedule(kind,number,mask); end
                    end
                end
            endcase
            if(receiver) begin
                // PS releases this frame only after copying its pixels and quality map.
                if(start && done) begin done<=0; opened<=0; frame<=frame+1'b1;
                    verified<=0; valid_count<=0; missing<=0; end
                if(received && state==IDLE && !(start && done)) begin
                    reply_session<=rx_session; reply_frame<=rx_frame;
                    if(rx_kind==OPEN && !done && rx_frame==frame && rx_number>frame &&
                       (!bound || (rx_session==session && rx_number==count))) begin
                        bound<=1; opened<=1; session<=rx_session; count<=rx_number;
                        schedule(READY,rx_number,0);
                    end else if(opened && match_id && !done && rx_kind==DATA && rx_number<PACKETS) begin
                        if (!verified[rx_number[8:0]]) begin
                            copy_packet<=rx_number[8:0]; copy_good<=payload_good;
                            copying<=1; copy_index<=0;
                        end else retries<=retries+1'b1;
                    end else if(opened && match_id && !done && rx_kind==POLL && rx_number<PACKETS) begin
                        schedule(SACK,rx_number,ack_bits[rx_number[8:0] +: 8]);
                    end else if(opened && match_id && rx_kind==FIN) begin
                        done<=1; missing<=PACKETS-valid_count; schedule(CLOSED,0,0);
                    end else if(bound && rx_session==session && frame!=0 && rx_frame==frame-1'b1 && rx_kind==FIN)
                        schedule(CLOSED,0,0);
                end
            end
        end
    end
endmodule

