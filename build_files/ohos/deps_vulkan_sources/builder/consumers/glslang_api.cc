// SPDX-License-Identifier: GPL-2.0-or-later
#include <glslang/Public/ShaderLang.h>
#include <glslang/Public/ResourceLimits.h>
#include <cstdio>
int main(){
  if(!glslang::InitializeProcess() || !GetDefaultResources())return 1;
  { glslang::TShader shader(EShLangCompute);const char* text="#version 450\nlayout(local_size_x=1) in;void main(){}";shader.setStrings(&text,1);
    if(!shader.parse(GetDefaultResources(),450,false,EShMsgDefault)){glslang::FinalizeProcess();return 2;} }
  glslang::FinalizeProcess();std::puts("{\"isolatedUpstreamGlslangPublicAPI\":true}");return 0;
}
