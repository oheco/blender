#include <stdio.h>
#include <stdint.h>
#include <string.h>
#ifndef __OHOS__
#error Native OHOS compiler required
#endif
#ifndef __aarch64__
#error Native ARM64 required
#endif
int main(void) {
    double value=1.0;unsigned char bytes[sizeof(value)];uint32_t one=1;
    memcpy(bytes,&value,sizeof(value));
    printf("{\"little_endian\":%s,\"pointer_bytes\":%zu,\"double_bytes\":\"",*(unsigned char*)&one?"true":"false",sizeof(void*));
    for(size_t i=0;i<sizeof(value);++i)printf("%02x",bytes[i]);
    puts("\"}");return 0;
}
