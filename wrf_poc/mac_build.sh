#!/usr/bin/env bash
# WRF 4.5.2 natively on the Mac mini (the owner's OK, 23.10.2026): dmpar, gfortran + Open MPI from Homebrew
# (NCAR's ncar/iwrf image is amd64 only). 23.10.2026: compiled in 521 s, no errors (gfortran 16.2, Open MPI 5.0.11).
# Needs: brew install gcc open-mpi netcdf netcdf-fortran, and the source in ~/wrf/WRF:
#   git clone --depth 1 --recurse-submodules --shallow-submodules --branch v4.5.2 https://github.com/wrf-model/WRF.git
# configure option 35: Darwin, GNU (gfortran/gcc), Open MPI, dmpar; nesting 1 (basic).
export PATH=/opt/homebrew/bin:$PATH
export NETCDF=$HOME/wrf/netcdf
export WRFIO_NCD_LARGE_FILE_SUPPORT=1
# WRF wants NetCDF's C and Fortran parts in one folder; Homebrew keeps them apart: linked together.
mkdir -p "$NETCDF/include" "$NETCDF/lib" "$NETCDF/bin"
for prefix in "$(brew --prefix netcdf)" "$(brew --prefix netcdf-fortran)"; do
  for part in include lib bin; do ln -sf "$prefix/$part"/* "$NETCDF/$part/"; done
done
cd ~/wrf/WRF
./clean -a > /dev/null 2>&1
printf '35\n1\n' | ./configure > ~/wrf/configure.log 2>&1
echo "configure exit $?"
grep -E "^(SFC|SCC|CCOMP|DM_FC|DM_CC|FCOPTIM|FCBASEOPTS_NO_G|CPP) " configure.wrf
start=$(date +%s)
./compile -j 6 em_real > ~/wrf/compile.log 2>&1
echo "compile took $(( $(date +%s) - start )) s"
ls -la main/*.exe 2>&1
echo BUILD-DONE
