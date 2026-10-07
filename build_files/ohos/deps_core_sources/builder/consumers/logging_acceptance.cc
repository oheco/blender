#include <gflags/gflags.h>
#include <glog/logging.h>
#include <cassert>
#include <iostream>
#include <string>
#include <thread>
#include <atomic>
DEFINE_int32(core_probe_number,3,"native closure flag");
class Sink final:public google::LogSink {
public:
  std::atomic<int> count{0};
  void send(google::LogSeverity severity,const char*,const char*,int,const struct tm*,const char* message,size_t length) override {
    if(severity==google::GLOG_INFO && std::string(message,length).find("core worker")!=std::string::npos)++count;
  }
};
int main(int argc,char**argv) {
  google::InitGoogleLogging(argv[0]);
  FLAGS_logtostderr=true;
  char program[]="logging-acceptance";char flag[]="--core_probe_number=41";char* arguments[]={program,flag,nullptr};char** av=arguments;int ac=2;
  gflags::ParseCommandLineFlags(&ac,&av,true);
  assert(FLAGS_core_probe_number==41 && ac==1);
  Sink sink;google::AddLogSink(&sink);
  std::thread t([]{LOG(INFO)<<"core worker signed native closure";});t.join();
  google::RemoveLogSink(&sink);
  assert(sink.count==1);
  std::string result=gflags::SetCommandLineOption("core_probe_number","invalid_integer");
  assert(result.empty() && FLAGS_core_probe_number==41);
  google::ShutdownGoogleLogging();
  std::cout<<"gflags native namespace=parse/invalid-value, glog thread LogSink=PASS\n";
}
