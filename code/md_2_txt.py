import os

def md_to_txt_in_folder(input_dir: str,out_dir:str):
    # 输出txt子文件夹
    txt_out_dir = out_dir
    os.makedirs(txt_out_dir, exist_ok=True)

    # 遍历文件夹下所有md文件
    for filename in os.listdir(input_dir):
        if not filename.lower().endswith(".md"):
            continue
        md_path = os.path.join(input_dir, filename)

        # 读取第一行，提取标题
        title = None
        with open(md_path, "r", encoding="utf-8") as f:
            first_line = f.readline()
            if first_line.startswith("# "):
                title = first_line.lstrip("# ").strip()
        
        if not title:
            print(f"跳过 {filename}：第一行没有 # 标题")
            continue

        # 读取全部md内容
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        
        # 输出txt路径
        txt_filename = f"{title}.txt"
        txt_path = os.path.join(txt_out_dir, txt_filename)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        
        print(f"转换成功：{md_path} -> {txt_path}")
        
def process_txt_paragraphs(folder_path):
    # 遍历文件夹内所有txt文件
    for filename in os.listdir(folder_path):
        if not filename.lower().endswith(".txt"):
            continue
        file_path = os.path.join(folder_path, filename)

        # 读取原始内容
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            stripped_line = line.strip()
            # 匹配条件：# / ## / ###  或者 第x步、第 x 步
            if (stripped_line.startswith(("# ", "## ", "### ")) or
                stripped_line.startswith("第") and "步" in stripped_line):
                # 匹配成功，前面增加两个空行
                new_lines.extend(["\n", "\n"])
            new_lines.append(line)

        # 覆盖写回原文件
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"已处理: {file_path}")
        

if __name__ == "__main__":
    # 目标文件夹 data\output
    input_dir = r"data\output"
    out_dir = os.path.join(input_dir, "txt")
    # md_to_txt_in_folder(input_dir,out_dir)
    process_txt_paragraphs(out_dir)
