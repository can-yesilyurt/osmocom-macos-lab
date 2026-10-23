/* SPDX-License-Identifier: MIT
 * Synthetic GTP-C/GTP-U gateway check, using the installed native libgtp.
 * Does not read subscriber data or open the radio. DNS and a small HTTP request
 * exercise the return path without changing routes or creating another TUN.
 */
#include <arpa/inet.h>
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>
#include <osmocom/core/application.h>
#include <osmocom/core/logging.h>
#include <osmocom/gtp/gtp.h>
#include <osmocom/gtp/gsn.h>

static struct gsn_t *gsn;
static struct pdp_t *context;
static struct in_addr target;
static int accepted, replies, deleted, synacked, http_ok, http_status;
static const char *http_host;
static struct in_addr http_target;
static uint32_t server_next;
static uint32_t client_next = 0x12340001;
static const struct log_info log_info = {0};

static void put16(uint8_t *p, unsigned value) { p[0]=value>>8; p[1]=value; }
static void put32(uint8_t *p, uint32_t value) {p[0]=value>>24;p[1]=value>>16;p[2]=value>>8;p[3]=value;}
static uint32_t get32(const uint8_t *p) {return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];}
static uint16_t checksum(const uint8_t *p, size_t n) {
    uint32_t sum=0;
    while(n>1) {sum+=((unsigned)p[0]<<8)|p[1];p+=2;n-=2;}
    if(n)sum+=(unsigned)*p<<8;
    while(sum>>16)sum=(sum&65535)+(sum>>16);
    return (uint16_t)~sum;
}
static double now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void send_dns(void) {
    static const uint8_t dns[] = {0x52,0xa9,1,0,0,1,0,0,0,0,0,0,7,'e','x','a','m','p','l','e',3,'c','o','m',0,0,1,0,1};
    uint8_t p[20+8+sizeof(dns)]={0};
    p[0]=0x45;put16(p+2,sizeof(p));p[8]=64;p[9]=17;
    memcpy(p+12,context->eua.v+2,4);memcpy(p+16,&target,4);
    put16(p+10,checksum(p,20));put16(p+20,49152);put16(p+22,53);put16(p+24,8+sizeof(dns));
    memcpy(p+28,dns,sizeof(dns));
    if(gtp_data_req(gsn,context,p,sizeof(p))<0)fprintf(stderr,"DNS send failed\n");
    else puts("DNS query sent through GTP-U");
}
static void send_tcp(uint8_t flags,const char *body) {
    size_t n=body?strlen(body):0;
    uint8_t p[1024]={0},pseudo[1024]={0};
    if(n>sizeof(p)-40)return;
    unsigned h=flags==2?24:20;
    p[0]=0x45;put16(p+2,20+h+n);p[8]=64;p[9]=6;
    memcpy(p+12,context->eua.v+2,4);memcpy(p+16,&http_target,4);put16(p+10,checksum(p,20));
    uint8_t *t=p+20;put16(t,49153);put16(t+2,80);
    put32(t+4,flags==2?client_next-1:client_next);put32(t+8,flags==2?0:server_next);
    t[12]=(h/4)<<4;t[13]=flags;put16(t+14,32768);
    if(h==24) {t[20]=2;t[21]=4;put16(t+22,1200);}
    if(n)memcpy(t+h,body,n);
    memcpy(pseudo,p+12,8);pseudo[9]=6;put16(pseudo+10,h+n);memcpy(pseudo+12,t,h+n);
    put16(t+16,checksum(pseudo,12+h+n));
    gtp_data_req(gsn,context,p,20+h+n);
    if(n)client_next+=n;
}
static int confirm(int type,int cause,struct pdp_t *pdp,void *cbp) {
    (void)cbp;
    if(type==GTP_CREATE_PDP_REQ) {
        printf("PDP creation cause=%d\n",cause);
        if(cause==128 && pdp && pdp->eua.l==6) {context=pdp;accepted=1;}
    } else if(type==GTP_DELETE_PDP_REQ) {deleted=1;context=NULL;}
    return 0;
}
static int data(struct pdp_t *pdp,void *packet,unsigned len) {
    (void)pdp;const uint8_t *p=packet;
    printf("GTP-U response received: bytes=%u protocol=%u\n",len,len>9?p[9]:0);
    if(len>=40 && p[9]==6) {
        unsigned ih=(p[0]&15)*4;
        if(ih<20 || len<ih+20)return 0;
        const uint8_t *t=p+ih;unsigned th=(t[12]>>4)*4;
        if(t[0]!=0 || t[1]!=80 || t[2]!=0xc0 || t[3]!=1 || th<20 || len<ih+th)return 0;
        if((t[13]&0x12)==0x12 && get32(t+8)==client_next && !synacked) {
            synacked=1;server_next=get32(t+4)+1;puts("TCP SYN/ACK received");
            char request[512];
            int count=snprintf(request,sizeof(request),"GET / HTTP/1.1\r\nHost: %s\r\nUser-Agent: osmocom-macos-lab-connectivity-check\r\nConnection: close\r\n\r\n",http_host);
            if(count<0 || count>=(int)sizeof(request))return 0;
            send_tcp(0x10,NULL);send_tcp(0x18,request);
        } else if(len>ih+th && synacked) {
            const uint8_t *body=t+th;size_t n=len-ih-th;
            if(n>=12 && !memcmp(body,"HTTP/1.",7)) {
                if(isdigit(body[9]) && isdigit(body[10]) && isdigit(body[11])) {
                    http_status=(body[9]-'0')*100+(body[10]-'0')*10+body[11]-'0';
                    http_ok=http_status>=100 && http_status<=599;
                    printf("HTTP status: %d\n",http_status);
                }
            }
            server_next=get32(t+4)+n;send_tcp(0x10,NULL);
        }
        return 0;
    }
    if(len<20 || (p[0]>>4)!=4 || p[9]!=17)return 0;
    unsigned h=(p[0]&15)*4;
    if(h<20 || len<h+20)return 0;
    const uint8_t *udp=p+h,*dns=udp+8;
    if(udp[0]!=0 || udp[1]!=53 || udp[2]!=0xc0 || udp[3]!=0)return 0;
    if(dns[0]==0x52 && dns[1]==0xa9 && (dns[2]&0x80) && !(dns[3]&15) && (dns[6]||dns[7])) {
        replies++;puts("Valid DNS A response for example.com received");
    }
    return 0;
}
static void pump(void) {
    fd_set r;FD_ZERO(&r);FD_SET(gsn->fd0,&r);FD_SET(gsn->fd1c,&r);FD_SET(gsn->fd1u,&r);
    int max=gsn->fd0;if(gsn->fd1c>max)max=gsn->fd1c;if(gsn->fd1u>max)max=gsn->fd1u;
    struct timeval wait={0,100000};
    if(select(max+1,&r,NULL,NULL,&wait)>0) {
        if(FD_ISSET(gsn->fd0,&r))gtp_decaps0(gsn);
        if(FD_ISSET(gsn->fd1c,&r))gtp_decaps1c(gsn);
        if(FD_ISSET(gsn->fd1u,&r))gtp_decaps1u(gsn);
    }
}
int main(int argc,char **argv) {
    if(argc!=6) {fprintf(stderr,"usage: probe STATE_DIR APN DNS_IPV4 HTTP_IPV4 HTTP_HOST\n");return 2;}
    if(!*argv[2] || strlen(argv[2])>63 || !*argv[5] || strlen(argv[5])>253 ||
       strspn(argv[5],"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")!=strlen(argv[5]) ||
       inet_pton(AF_INET,argv[3],&target)!=1 || inet_pton(AF_INET,argv[4],&http_target)!=1)return 2;
    http_host=argv[5];setvbuf(stdout,NULL,_IOLBF,0);
    if(osmo_init_logging2(NULL,&log_info)<0)return 2;
    log_set_log_level(osmo_stderr_target,LOGL_NOTICE);
    struct in_addr local,remote;inet_pton(AF_INET,"127.0.0.2",&local);inet_pton(AF_INET,"127.0.0.1",&remote);
    if(gtp_new(&gsn,argv[1],&local,GTP_MODE_SGSN)<0)return 2;
    gtp_set_cb_conf(gsn,confirm);gtp_set_cb_data_ind(gsn,data);
    if(gtp_pdp_newpdp(gsn,&context,UINT64_C(0xf100000000010100),5,NULL)<0)return 2;
    context->version=1;context->hisaddr0=remote;context->hisaddr1=remote;
    context->gsnlc.l=4;memcpy(context->gsnlc.v,&local,4);context->gsnlu=context->gsnlc;
    const char *label=argv[2];uint8_t *apn=context->apn_use.v;
    while(*label) {
        const char *dot=strchr(label,'.');size_t n=dot?(size_t)(dot-label):strlen(label);
        if(!n || n>63)return 2;
        *apn++=n;memcpy(apn,label,n);apn+=n;
        if(!dot)break;label=dot+1;
    }
    context->apn_use.l=apn-context->apn_use.v;
    context->eua.l=2;context->eua.v[0]=PDP_EUA_ORG_IETF;context->eua.v[1]=PDP_EUA_TYPE_v4;
    context->qos_req.l=4;memcpy(context->qos_req.v,"\x00\x0b\x92\x1f",4);
    context->msisdn.l=3;memcpy(context->msisdn.v,"\x91\x01\x00",3);
    if(gtp_create_context_req(gsn,context,NULL)<0)return 2;
    double end=now()+8,next=0;int sent=0;
    while(now()<end && !replies) {pump();if(accepted && sent<3 && now()>=next) {send_dns();sent++;next=now()+1;}}
    if(accepted) {
        puts("Sending HTTP TCP SYN through GTP-U");send_tcp(2,NULL);end=now()+10;
        double retry=now()+2;int retries=0;
        while(now()<end && !http_ok) {pump();if(!synacked && now()>=retry && retries<3) {send_tcp(2,NULL);retries++;retry=now()+2;}}
        if(synacked)send_tcp(0x11,NULL);
    }
    if(accepted && context) {gtp_delete_context_req2(gsn,context,NULL,1);end=now()+2;while(now()<end && !deleted)pump();}
    printf("RESULT accepted=%d dns_replies=%d tcp=%d http=%d http_status=%d\n",accepted,replies,synacked,http_ok,http_status);gtp_free(gsn);
    return accepted && replies && http_ok?0:1;
}
