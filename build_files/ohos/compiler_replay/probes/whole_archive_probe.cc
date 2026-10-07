#include <cstdio>
extern "C" {int whole_archive_marker=0;}
int main(){std::printf("unreferenced archive registration marker=%d\n",whole_archive_marker);return whole_archive_marker==73?0:1;}
