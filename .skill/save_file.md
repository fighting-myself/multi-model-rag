# save_file

将用户要求保存的文本（如总结、报告）写入服务器项目 **data** 目录下的文件。

- **工具名**：`file_write`
- **参数**：
  - `path`：相对 data 目录的文件路径，例如 `conclusion.txt`、`reports/summary.txt`。用户若指定了完整路径如 `/root/.../data/conclusion.txt`，只取最后相对 data 的部分，即 `conclusion.txt`。
  - `content`：要保存的完整文本内容。
- **用法**：当用户说「保存到 data 目录」「保存到服务器」「保存为 xxx.txt」「把总结写到 conclusion.txt」等时，先完成前面的任务（如打开网页、总结），再将得到的文本通过 `file_write` 写入指定路径。
