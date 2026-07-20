import sys

def fix_literal_newlines(input_path: str, output_path: str = None) -> None:
    """
    读取 input_path 的 Markdown 文件，将其中所有的字面字符串 \n (两个字符) 
    替换为真正的换行符，并将结果写入 output_path。
    若未提供 output_path，则自动生成 '原文件名_fixed.md'。
    """
    # 读取文件内容
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 将字面 "\n" 替换为真正的换行符
    # 注意：Python 字符串里 "\\n" 表示反斜杠 + n，替换为 "\n" 是真正的换行
    fixed_content = content.replace('\\n', '\n')
    
    # 确定输出文件名
    if output_path is None:
        if input_path.endswith('.md'):
            output_path = input_path[:-3] + '_fixed.md'
        else:
            output_path = input_path + '_fixed'
    
    # 写入新文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    
    print(f"处理完成，结果已保存至: {output_path}")

if __name__ == '__main__':
    # 用法：python fix_md_newlines.py 文件.md [输出文件.md]
    if len(sys.argv) < 2:
        print("用法: python fix_md_newlines.py <输入文件.md> [输出文件.md]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    fix_literal_newlines(input_file, output_file)