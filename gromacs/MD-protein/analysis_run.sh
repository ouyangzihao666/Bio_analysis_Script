#!/bin/bash

# 作者：欧阳梓豪 & TraeCN & 豆包-专家模式
# 版本: 1.0.1

# GROMACS分析脚本
# 使用方法: ./run_analysis.sh <config_file> <analysis_type> [protein_name]

# 错误处理设置
set -euo pipefail

# 检查参数
if [ $# -lt 2 ]; then
    echo "用法: $0 <配置文件> <分析类型> [蛋白质名称]"
    echo "可用分析类型:"
    echo "  rmsd       - 均方根偏差"
    echo "  rmsf       - 均方根波动" 
    echo "  gyrate     - 回转半径"
    echo "  hbond      - 氢键分析"
    echo "  dssp       - 二级结构分析"
    echo "  all        - 运行所有分析"
    echo ""
    echo "示例:"
    echo "  $0 config.conf rmsd              # 分析所有蛋白质的RMSD"
    echo "  $0 config.conf rmsd 1AKI         # 只分析1AKI的RMSD"
    echo "  $0 config.conf all               # 运行所有分析"
    exit 1
fi

CONFIG_FILE="$1"
ANALYSIS_TYPE="$2"
SPECIFIC_PROTEIN="${3:-}"

# 设置默认值
SKIP_EXISTING="true"
ANALYSIS_DIR="analysis"
TRAJ_SUFFIX="_md_noPBC.xtc"
TPR_SUFFIX="_md.tpr"

# 加载配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "错误: 配置文件 $CONFIG_FILE 不存在"
    exit 1
fi
source "$CONFIG_FILE"

# 检查必要参数
check_required_param() {
    if [ -z "${!1}" ]; then
        echo "错误: 配置文件中缺少必要参数: $1"
        exit 1
    fi
}

required_params=("PROTEIN_LIST" "WORK_DIR")
for param in "${required_params[@]}"; do
    check_required_param "$param"
done

# 创建分析目录
mkdir -p "$WORK_DIR/$ANALYSIS_DIR"
cd "$WORK_DIR/$ANALYSIS_DIR"

# 函数：检查文件是否存在，如果存在则跳过步骤
check_and_skip() {
    local file="$1"
    local step="$2"
    
    if [ "$SKIP_EXISTING" = "true" ] && [ -f "$file" ]; then
        echo "跳过 $step - 文件已存在: $file"
        return 0
    else
        return 1
    fi
}

# 函数：运行GROMACS命令，检查是否成功
run_gmx() {
    local cmd="$1"
    local output_file="$2"
    local step="$3"
    
    echo "运行: $step"
    if eval "$cmd"; then
        if [ -n "$output_file" ] && [ -f "$output_file" ]; then
            echo "完成: $step"
            return 0
        else
            echo "警告: 命令执行成功但输出文件未找到: $output_file"
            return 1
        fi
    else
        echo "错误: $step 失败"
        return 1
    fi
}

# 解析蛋白质列表
IFS=',' read -ra PROTEINS <<< "$PROTEIN_LIST"

# 如果指定了特定蛋白质，只处理该蛋白质
if [ -n "$SPECIFIC_PROTEIN" ]; then
    FILTERED_PROTEINS=()
    for protein in "${PROTEINS[@]}"; do
        protein=$(echo "$protein" | xargs)
        protein_name=$(basename "$protein" .pdb)
        if [ "$protein_name" = "$SPECIFIC_PROTEIN" ]; then
            FILTERED_PROTEINS=("$protein")
            break
        fi
    done
    if [ ${#FILTERED_PROTEINS[@]} -eq 0 ]; then
        echo "错误: 未找到蛋白质 $SPECIFIC_PROTEIN"
        exit 1
    fi
    PROTEINS=("${FILTERED_PROTEINS[@]}")
fi

# RMSD分析函数
analyze_rmsd() {
    local protein_name="$1"
    local analysis_dir="$2"
    local fit_GROUP="$3"
    local cal_GROUP="$4"


    # cd "$analysis_dir"
    
    local input_tpr="../../${protein_name}/08_md_production/${protein_name}${TPR_SUFFIX}"
    local input_traj="../../${protein_name}/08_md_production/${protein_name}${TRAJ_SUFFIX}"
    local output_file="${protein_name}_rmsd.xvg"
    
    if ! check_and_skip "$output_file" "RMSD分析"; then
        run_gmx "echo -e '$fit_GROUP\\n$cal_GROUP' | gmx rms -s '$input_tpr' -f '$input_traj' -o '$output_file' -tu ns" \
                "$output_file" "RMSD分析"
    fi
    
    
}

# RMSF分析函数  
analyze_rmsf() {
    local protein_name="$1"
    local analysis_dir="$2"
    local cal_GROUP="$3"


    local input_tpr="../../${protein_name}/08_md_production/${protein_name}${TPR_SUFFIX}"
    local input_traj="../../${protein_name}/08_md_production/${protein_name}${TRAJ_SUFFIX}"
    local output_file="${protein_name}_rmsf.xvg"
    
    if ! check_and_skip "$output_file" "RMSF分析"; then
        run_gmx "echo '$cal_GROUP' | gmx rmsf -s '$input_tpr' -f '$input_traj' -o '$output_file' -res" \
                "$output_file" "RMSF分析"
    fi
    
}

# 回转半径分析函数
analyze_gyrate() {
    local protein_name="$1"
    local analysis_dir="$2"
    local cal_GROUP="$3"

    
    local input_tpr="../../${protein_name}/08_md_production/${protein_name}${TPR_SUFFIX}"
    local input_traj="../../${protein_name}/08_md_production/${protein_name}${TRAJ_SUFFIX}"
    local output_file="${protein_name}_gyrate.xvg"
    
    if ! check_and_skip "$output_file" "回转半径分析"; then
        run_gmx "echo '$cal_GROUP' | gmx gyrate -s '$input_tpr' -f '$input_traj' -o '$output_file' -tu ns" \
                "$output_file" "回转半径分析"
    fi
    
}

# 氢键分析函数
analyze_hbond() {
    local protein_name="$1"
    local analysis_dir="$2"
    local ref_GROUP="$3"
    local tar_GROUP="$4"
    
    
    local input_tpr="../../${protein_name}/08_md_production/${protein_name}${TPR_SUFFIX}"
    local input_traj="../../${protein_name}/08_md_production/${protein_name}${TRAJ_SUFFIX}"
    local output_file="${protein_name}_hbnum.xvg"
    
    if ! check_and_skip "$output_file" "氢键分析"; then
        run_gmx "echo -e '$ref_GROUP\\n$tar_GROUP' | gmx hbond -s '$input_tpr' -f '$input_traj' -num '$output_file' -tu ns" \
                "$output_file" "氢键分析"
    fi
    
}

# 二级结构分析函数
analyze_dssp() {
    local protein_name="$1"
    local analysis_dir="$2"
    local cal_GROUP="$3"

           
    local input_tpr="../../${protein_name}/08_md_production/${protein_name}${TPR_SUFFIX}"
    local input_traj="../../${protein_name}/08_md_production/${protein_name}${TRAJ_SUFFIX}"
    local output_file="${protein_name}_dssp.dat"
    local output_num="${protein_name}_dssp_num.xvg"
    
    if ! check_and_skip "$output_file" "二级结构分析"; then
        run_gmx "echo '$cal_GROUP' | gmx dssp -s '$input_tpr' -f '$input_traj' -o '$output_file' -num '$output_num' -tu ns" \
                "$output_file" "二级结构分析"
    fi
    
}

# 根据分析类型执行相应分析
case "$ANALYSIS_TYPE" in
    "rmsd")
        ANALYSIS_DIR_NAME="rmsd"
        mkdir -p "$ANALYSIS_DIR_NAME"
        cd "$ANALYSIS_DIR_NAME"

        if [ -z "$RMSD_fit_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: RMSD_fit_GROUP"
        exit 1
        fi
        if [ -z "$RMSD_cal_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: RMSD_cal_GROUP"
        exit 1
        fi
        
        for protein in "${PROTEINS[@]}"; do
            protein=$(echo "$protein" | xargs)
            protein_name=$(basename "$protein" .pdb)
            echo "分析蛋白质 $protein_name 的RMSD"
            analyze_rmsd "$protein_name" "$ANALYSIS_DIR_NAME" "$RMSD_fit_GROUP" "$RMSD_cal_GROUP"
        done
        cd ..
        ;;
        
    "rmsf")
        ANALYSIS_DIR_NAME="rmsf"
        mkdir -p "$ANALYSIS_DIR_NAME"
        cd "$ANALYSIS_DIR_NAME"

        if [ -z "$RMSD_fit_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: RMSF_cal_GROUP"
        exit 1
        fi
        
        for protein in "${PROTEINS[@]}"; do
            protein=$(echo "$protein" | xargs)
            protein_name=$(basename "$protein" .pdb)
            echo "分析蛋白质 $protein_name 的RMSF"
            analyze_rmsf "$protein_name" "$ANALYSIS_DIR_NAME" "$RMSF_cal_GROUP"
        done
        cd ..
        ;;
        
    "gyrate")
        ANALYSIS_DIR_NAME="gyrate"
        mkdir -p "$ANALYSIS_DIR_NAME"
        cd "$ANALYSIS_DIR_NAME"

        if [ -z "$RMSD_fit_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: GYRATE_cal_GROUP"
        exit 1
        fi
        
        for protein in "${PROTEINS[@]}"; do
            protein=$(echo "$protein" | xargs)
            protein_name=$(basename "$protein" .pdb)
            echo "分析蛋白质 $protein_name 的回转半径"
            analyze_gyrate "$protein_name" "$ANALYSIS_DIR_NAME" "$GYRATE_cal_GROUP"
        done
        cd ..
        ;;
        
    "hbond")
        ANALYSIS_DIR_NAME="hbond"
        mkdir -p "$ANALYSIS_DIR_NAME"
        cd "$ANALYSIS_DIR_NAME"

        if [ -z "$RMSD_fit_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: HBOND_Ref_GROUP"
        exit 1
        fi
        if [ -z "$RMSD_fit_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: HBOND_Target_GROUP"
        exit 1
        fi
        
        for protein in "${PROTEINS[@]}"; do
            protein=$(echo "$protein" | xargs)
            protein_name=$(basename "$protein" .pdb)
            echo "分析蛋白质 $protein_name 的氢键"
            analyze_hbond "$protein_name" "$ANALYSIS_DIR_NAME" "$HBOND_Ref_GROUP" "$HBOND_Target_GROUP"
        done
        cd ..
        ;;
        
    "dssp")
        ANALYSIS_DIR_NAME="dssp"
        mkdir -p "$ANALYSIS_DIR_NAME"
        cd "$ANALYSIS_DIR_NAME"

        if [ -z "$DSSP_cal_GROUP" ]; then
        echo "错误: 配置文件中缺少必要参数: DSSP_cal_GROUP"
        exit 1
        fi
        
        for protein in "${PROTEINS[@]}"; do
            protein=$(echo "$protein" | xargs)
            protein_name=$(basename "$protein" .pdb)
            echo "分析蛋白质 $protein_name 的二级结构"
            analyze_dssp "$protein_name" "$ANALYSIS_DIR_NAME" "$DSSP_cal_GROUP"
        done
        cd ..
        ;;
        
    "all")
        # 运行所有分析
        for analysis in rmsd rmsf gyrate hbond dssp; do
            echo "执行分析: $analysis"
            "$0" "$CONFIG_FILE" "$analysis" "$SPECIFIC_PROTEIN"
        done
        ;;
        
    *)
        echo "错误: 未知的分析类型 '$ANALYSIS_TYPE'"
        echo "可用分析类型: rmsd, rmsf, gyrate, hbond, dssp, all"
        exit 1
        ;;
esac

echo "分析完成: $ANALYSIS_TYPE"