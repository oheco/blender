#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Materialize complete pristine volume archives offline; not a native builder."""
import argparse,hashlib,importlib.util,json,os,pathlib,sys
sys.dont_write_bytecode=True

def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--repository-root',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[3])
 parser.add_argument('--destination',type=pathlib.Path,required=True)
 args=parser.parse_args();root=args.repository_root.resolve();dest=args.destination.resolve()
 allowed=[pathlib.Path(os.environ[k]).resolve() for k in ['XDG_CACHE_HOME','TMPDIR']]
 if not any(dest.is_relative_to(p) and dest!=p for p in allowed):raise SystemExit('Use a private source cache or TMPDIR child directory')
 file=root/'build_files/ohos/deps_volume_sources/provenance.json'
 provenance=json.loads(file.read_text())
 if provenance['offline_builder_completed'] or provenance['status']!='source-only prepared':raise SystemExit('Unexpected provenance scope')
 spec=importlib.util.spec_from_file_location('volume_vendor_archive',root/'build_files/ohos/vendor_archive.py');vendor=importlib.util.module_from_spec(spec);spec.loader.exec_module(vendor)
 records=[]
 for row in provenance['sources']:
  relative=pathlib.PurePosixPath(row['registry_manifest'])
  if relative.is_absolute() or '..' in relative.parts:raise SystemExit('Unsafe registry-relative path')
  directory=(root/pathlib.Path(relative)).parent
  if not directory.resolve().is_relative_to(root/'tpr/sources'):raise SystemExit('Registry source path escaped repository')
  output=dest/row['archive_filename'];vendor.safe_name(output.name)
  manifest=vendor.materialize(directory,output)
  if manifest['sha256']!=row['sha256'] or manifest['size']!=row['size'] or digest(output)!=row['sha256']:raise SystemExit('Frozen original archive lineage mismatch')
  records.append({'name':row['name'],'archive':output.name,'sha256':row['sha256'],'size':row['size']})
 # Patches/notices are repository-relative independent inputs; materialization
 # never replaces a pristine archive or compiles library code.
 for row in provenance['patches']+provenance['supplementary_notices']+[provenance['original_blender_patch']]:
  p=pathlib.PurePosixPath(row['path'])
  if p.is_absolute() or '..' in p.parts or digest(root/pathlib.Path(p))!=row['sha256']:raise SystemExit('Source adaptation/notice drift')
 print(json.dumps({'source_only_prepared':True,'offline_builder_completed':False,'complete_original_archives':records,'network_used':False,'pristine_archives_patched':False},indent=2))
if __name__=='__main__':main()
