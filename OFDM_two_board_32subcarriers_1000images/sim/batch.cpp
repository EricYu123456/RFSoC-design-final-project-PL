// Two production board wrappers and synchronous BRAM models. Only the
// high-rate OFDM PHY is replaced when elaborating this accelerated test.
#include "Vofdm_rf_board.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

constexpr size_t pixels=307200, descriptors=0x7fff0;
struct Simulation {
    Vofdm_rf_board a, b;
    std::vector<uint8_t> source=std::vector<uint8_t>(524288);
    std::vector<uint8_t> sink=std::vector<uint8_t>(524288);
    uint64_t cycles=0, polls=0, sacks=0;
    int forward_pos=-1, reverse_pos=-1;
    unsigned fault=0, forward_kind=0, reverse_kind=0, packet_number=0;
    unsigned altered=0, lost_ack=0, lost_close=0;
    bool released=false, cut=false;
    void impair(uint32_t &f, uint32_t &r) {
        if(f&256) {
            if(forward_pos==2) forward_kind=f&255;
            if(forward_pos==9) packet_number=(f&255)<<8;
            if(forward_pos==10) packet_number|=f&255;
            if(forward_kind==0 && packet_number==7 && forward_pos==96 &&
               (fault==2 || (fault==1 && !altered))) { f^=1; ++altered; }
            if(fault==3 && forward_pos==2 && forward_kind==0) cut=true;
        }
        if(r&256) {
            if(reverse_pos==2) reverse_kind=r&255;
            if(fault==1 && reverse_pos==15 && reverse_kind==4 && !lost_ack) {r^=1; ++lost_ack;}
            if(fault==1 && reverse_pos==15 && reverse_kind==6 && !lost_close) {r^=1; ++lost_close;}
        }
        if(cut) f=r=0;
    }
    Simulation() {
        for(auto* board : {&a,&b}) {
            board->adc_i_valid=board->adc_q_valid=board->dac_ready=1;
            board->aresetn=0; board->control=1;
        }
        tick(); tick();
        a.aresetn=b.aresetn=1; a.control=0; b.control=8;
    }
    void tick() {
        if(a.dac_data && b.dac_data) throw std::runtime_error("airtime overlap");
        if(a.dac_data&512) forward_pos=0;
        if(b.dac_data&512) reverse_pos=0;
        if((a.dac_data&256) && forward_pos++==1 && (a.dac_data&255)==3) ++polls;
        if((b.dac_data&256) && reverse_pos++==1 && (b.dac_data&255)==4) ++sacks;
        uint32_t forward=a.dac_data,reverse=b.dac_data;
        if(fault) impair(forward,reverse);
        a.adc_i=reverse; b.adc_i=forward;
        a.clk=b.clk=0; a.eval(); b.eval();
        uint32_t word=0;
        if(a.src_en) std::memcpy(&word,source.data()+a.src_addr,4);
        if(b.dst_en) {
            if(b.dst_addr+3>=sink.size()) throw std::runtime_error("BRAM address");
            for(int lane=0;lane<4;++lane)
                if(b.dst_we & (1<<lane)) sink[b.dst_addr+lane]=b.dst_din>>(lane*8);
        }
        a.clk=b.clk=1; a.eval(); b.eval();
        a.src_dout=word;
        ++cycles;
        if(((a.status|b.status)&0x30) || ((a.status&2) && fault!=3)) throw std::runtime_error("hardware fault");
    }
    void frame(unsigned n, unsigned count,std::istream& input,std::ostream& output) {
        if(!input.read(reinterpret_cast<char*>(source.data()),pixels)) throw std::runtime_error("input truncated");
        const uint32_t header[2]={0x653d984au,(count<<16)|n};
        std::memcpy(source.data()+descriptors,header,sizeof(header));
        std::fill(sink.begin(),sink.end(),0);
        a.control=2; tick(); a.control=0;
        for(int t=0;t<20;++t) tick();
        const auto limit=cycles+(fault==3 ? 40000000 : 8000000);
        while(!((a.status&1) && ((b.status&1) || released))) {
            if(fault==3 && (a.status&2)) return;
            if(fault==1 && !released && (b.status&1)) {
                b.control=10; tick(); b.control=8; released=true;
            }
            tick(); if(cycles>limit) throw std::runtime_error("frame timeout " + std::to_string(n));
        }
        if(fault==3) throw std::runtime_error("disconnect reported success");
        if(fault==1 && (altered!=1 || lost_ack!=1 || lost_close!=1 || !released || a.retries!=3))
            throw std::runtime_error("selective retry or late close replay mismatch");
        if(fault==2) {
            if(altered!=11 || ((a.status>>6)&65535)!=1 || ((b.status>>6)&65535)!=1 ||
               sink[pixels+7]!=1 || sink[7*1024+80]!=(source[7*1024+80]^1))
                throw std::runtime_error("exhausted packet accounting mismatch");
            // Normalize only the two already-checked damaged fields for the
            // full remaining-image comparison below.
            sink[pixels+7]=2; sink[7*1024+80]^=1;
        }
        if(std::memcmp(source.data(),sink.data(),pixels) ||
           std::memcmp(sink.data()+descriptors,header,sizeof(header)) ||
           !std::all_of(sink.begin()+pixels,sink.begin()+pixels+300,[](auto q){return q==2;}))
            throw std::runtime_error("pixels, quality or descriptor mismatch " + std::to_string(n));
        output.write(reinterpret_cast<const char*>(sink.data()),pixels);
        b.control=10; tick(); b.control=8; tick();
    }
};
int main(int argc,char** argv) {
    if(argc!=4 && argc!=5) return 2;
    const unsigned count=std::stoul(argv[3]);
    std::ifstream input(argv[1],std::ios::binary); std::ofstream output(argv[2],std::ios::binary);
    output.exceptions(std::ios::badbit|std::ios::failbit);
    Simulation sim;
    if(argc==5) sim.fault=std::stoul(argv[4]);
    for(unsigned i=0;i<count;++i) {
        sim.frame(i,count,input,output);
        if((i+1)%50==0) std::cout<<"verified "<<i+1<<std::endl;
    }
    if(sim.fault) { std::cout<<"FAULT_PASS "<<sim.fault<<std::endl; return 0; }
    if(sim.polls!=38*count || sim.sacks!=sim.polls || input.peek()!=EOF) return 3;
    std::cout<<"PASS "<<count<<" frames "<<sim.polls<<" block acknowledgements "<<sim.cycles<<" cycles"<<std::endl;
}

