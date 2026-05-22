#!/bin/bash
# GROMACS 通用数据分析函数库
# 被 analysis_protein.sh 和 analysis_complex.sh 调用

set -euo pipefail

# ====================== 通用工具函数 ======================
check_and_skip() {
    local file="$1"
    local step="$2"
    
    if [ "$SKIP_EXISTING" = "true" ] && [ -f "$file" ]; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: 跳过 $step - 文件已存在: $file"
        return 0
    else
        return 1
    fi
}

run_gmx() {
    local cmd="$1"
    local output_file="$2"
    local step="$3"
    
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: 开始执行: $step"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] DEBUG: 执行命令: $cmd"
    
    if eval "$cmd"; then
        if [ -n "$output_file" ] && [ -f "$output_file" ]; then
            echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: 完成: $step"
            return 0
        else
            echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: 命令执行成功但输出文件未找到: $output_file"
            return 1
        fi
    else
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: 失败: $step"
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: 失败的命令: $cmd"
        return 1
    fi
}

# ====================== 核心分析函数 ======================
# RMSD分析
analyze_rmsd() {
    local input_tpr="$1"
    local input_traj="$2"
    local output_file="$3"
    local fit_group="$4"
    local cal_group="$5"
    
    if ! check_and_skip "$output_file" "RMSD分析"; then
        run_gmx "echo -e '$fit_group\n$cal_group' | gmx rms -s '$input_tpr' -f '$input_traj' -o '$output_file' -tu ns" \
                "$output_file" "RMSD分析"
    fi
}

# RMSF分析
analyze_rmsf() {
    local input_tpr="$1"
    local input_traj="$2"
    local output_file="$3"
    local cal_group="$4"
    
    if ! check_and_skip "$output_file" "RMSF分析"; then
        run_gmx "echo '$cal_group' | gmx rmsf -s '$input_tpr' -f '$input_traj' -o '$output_file' -res" \
                "$output_file" "RMSF分析"
    fi
}

# 回转半径分析
analyze_gyrate() {
    local input_tpr="$1"
    local input_traj="$2"
    local output_file="$3"
    local cal_group="$4"
    
    if ! check_and_skip "$output_file" "回转半径分析"; then
        run_gmx "echo '$cal_group' | gmx gyrate -s '$input_tpr' -f '$input_traj' -o '$output_file' -tu ns" \
                "$output_file" "回转半径分析"
    fi
}

# 氢键分析
analyze_hbond() {
    local input_tpr="$1"
    local input_traj="$2"
    local output_file="$3"
    local ref_group="$4"
    local tar_group="$5"
    
    if ! check_and_skip "$output_file" "氢键分析"; then
        run_gmx "echo -e '$ref_group\n$tar_group' | gmx hbond -s '$input_tpr' -f '$input_traj' -num '$output_file' -tu ns" \
                "$output_file" "氢键分析"
    fi
}

# 二级结构分析
analyze_dssp() {
    local input_tpr="$1"
    local input_traj="$2"
    local output_dat="$3"
    local output_num="$4"
    local cal_group="$5"
    
    if ! check_and_skip "$output_dat" "二级结构分析"; then
        run_gmx "echo '$cal_group' | gmx dssp -s '$input_tpr' -f '$input_traj' -o '$output_dat' -num '$output_num' -tu ns" \
                "$output_dat" "二级结构分析"
    fi
}