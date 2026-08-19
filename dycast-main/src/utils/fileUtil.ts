/** 文件保存的配置 */
export interface FileSaveOptions {
  name?: string; // 文件名称（不含扩展名）
  ext?: string; // 文件扩展名（如 '.json'）
  mimeType?: string; // 指定 MIME 类型，优先使用
  description?: string; // 保存对话框中文件类型描述
  forceFallback?: boolean; // 是否强制使用传统下载方式
  existStrategy?: 'prompt' | 'overwrite' | 'new'; // 文件存在时的处理方式
}

export interface FileSaveResult {
  success: boolean; // 是否保存成功
  fileHandle?: FileSystemFileHandle | null; // FSA 模式下的文件句柄
  error?: Error; // 错误对象
  message?: string; // 提示信息
}

// 常见扩展名与 MIME 类型的映射表
const mimeMap: Record<string, string> = {
  '.txt': 'text/plain',
  '.json': 'application/json',
  '.html': 'text/html',
  '.htm': 'text/html',
  '.csv': 'text/csv',
  '.xml': 'application/xml',
  '.js': 'application/javascript',
  '.ts': 'application/typescript',
  '.css': 'text/css',
  '.md': 'text/markdown',
  '.lrc': 'text/plain',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.zip': 'application/zip',
  '.mp3': 'audio/mpeg',
  '.mp4': 'video/mp4'
};

/**
 * FileSaver 文件保存工具
 *  - 支持现代浏览器的 File System Access API (只能在安全上下文使用https|localhost)
 *  - 支持传统下载方式
 *  - 支持自动根据扩展名识别 MIME 类型
 */
class FileSaver {
  static async save(content: string | Blob | ArrayBuffer, options: FileSaveOptions = {}): Promise<FileSaveResult> {
    const {
      name = 'untitled',
      ext = '',
      mimeType,
      description = 'File',
      forceFallback = false,
      existStrategy = 'prompt'
    } = options;

    const inferredMime = mimeType || mimeMap[ext.toLowerCase()] || 'application/octet-stream';
    const fullName = this.ensureExtension(name, ext);

    try {
      // 优先尝试服务端直接保存（彻底绕过浏览器下载拦截）
      if (forceFallback) {
        const serverResult = await this.saveWithServer(content, fullName).catch(() => null);
        if (serverResult && serverResult.success) return serverResult;
        // 服务端保存失败时，降级到 <a> 标签方式
        return this.saveWithFallback(content, { name: fullName, mimeType: inferredMime });
      }

      // 非强制 fallback 且支持 FSA，使用现代方式
      if (this.isFSAvailable()) {
        return await this.saveWithFSA(content, {
          name,
          ext,
          mimeType: inferredMime,
          description,
          existStrategy
        });
      }

      return this.saveWithFallback(content, { name: fullName, mimeType: inferredMime });
    } catch (error) {
      return {
        success: false,
        error: error as Error,
        message: '文件保存失败'
      };
    }
  }

  /**
   * 使用 File System Access API 保存文件
   * @param content
   * @param options
   * @returns
   */
  private static async saveWithFSA(
    content: string | Blob | ArrayBuffer,
    options: {
      name: string;
      ext: string;
      mimeType: string;
      description: string;
      existStrategy: 'prompt' | 'overwrite' | 'new';
    }
  ): Promise<FileSaveResult> {
    try {
      // 确保文件名有扩展名
      const fileName = this.ensureExtension(options.name, options.ext);
      let fileHandle: FileSystemFileHandle | null = null;

      // 如果不是强制创建新文件，可以选择已有文件
      if (options.existStrategy !== 'new') {
        try {
          [fileHandle] = await window.showOpenFilePicker({
            types: [
              {
                description: options.description,
                accept: { [options.mimeType]: [options.ext] }
              }
            ]
          });

          // 如果策略为提示且用户拒绝覆盖，则取消操作
          if (options.existStrategy === 'prompt' && !confirm(`文件 "${fileHandle.name}" 已存在，是否覆盖？`)) {
            return {
              success: false,
              fileHandle: null,
              message: '用户取消覆盖'
            };
          }
        } catch {
          // 未选择文件或用户取消，继续新建流程
        }
      }

      // 没有文件句柄时，新建文件
      if (!fileHandle) {
        fileHandle = await window.showSaveFilePicker({
          suggestedName: fileName,
          types: [
            {
              description: options.description,
              accept: { [options.mimeType]: [options.ext] }
            }
          ]
        });
      }
      // 获取可写流
      const writable = await fileHandle.createWritable();
      // 内容转换成 Blob
      const blob = this.normalizeContentToBlob(content, options.mimeType);
      // 写入内容
      await writable.write(blob);
      // 关闭流
      await writable.close();

      return {
        success: true,
        fileHandle,
        message: '文件保存成功'
      };
    } catch (error) {
      // 用户取消操作的处理
      if ((error as Error).name === 'AbortError') {
        return {
          success: false,
          fileHandle: null,
          message: '用户取消操作'
        };
      }
      throw error;
    }
  }

  /**
   * 通过服务端 API 直接保存文件到本地磁盘（绕过浏览器下载拦截）
   * 文件会保存到项目根目录下的 downloads/ 文件夹
   */
  private static async saveWithServer(
    content: string | Blob | ArrayBuffer,
    name: string
  ): Promise<FileSaveResult> {
    // 将内容转为 base64
    let base64: string;
    if (typeof content === 'string') {
      // 文本内容：使用 TextEncoder 编码后再转 base64
      const bytes = new TextEncoder().encode(content);
      base64 = this.uint8ArrayToBase64(bytes);
    } else if (content instanceof Blob) {
      const buffer = await content.arrayBuffer();
      base64 = this.uint8ArrayToBase64(new Uint8Array(buffer));
    } else {
      // ArrayBuffer
      base64 = this.uint8ArrayToBase64(new Uint8Array(content));
    }

    const res = await fetch('/api/save-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, content: base64 })
    });
    const json = await res.json();
    if (json.success) {
      return {
        success: true,
        fileHandle: null,
        message: `文件已保存到: ${json.path}`
      };
    }
    throw new Error(json.message || '服务端保存失败');
  }

  /** Uint8Array → base64 */
  private static uint8ArrayToBase64(bytes: Uint8Array): string {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  /**
   * 传统方式保存文件
   * @param content
   * @param options
   * @returns
   */
  private static saveWithFallback(
    content: string | Blob | ArrayBuffer,
    options: { name: string; mimeType: string }
  ): FileSaveResult {
    try {
      // 内容转为 Blob
      const blob = this.normalizeContentToBlob(content, options.mimeType);
      // 创建临时 URL
      const url = URL.createObjectURL(blob);
      // 创建 <a> 标签
      const a = document.createElement('a');
      // 设置下载链接
      a.href = url;
      // 设置下载文件名
      a.download = options.name;
      // 隐藏标签
      a.style.display = 'none';
      // 添加到页面中
      document.body.appendChild(a);
      // 触发下载
      a.click();
      // 清理标签
      document.body.removeChild(a);
      // 释放 URL
      setTimeout(() => {
        URL.revokeObjectURL(url);
      }, 60000);

      return {
        success: true,
        fileHandle: null,
        message: '文件下载开始'
      };
    } catch (error) {
      return {
        success: false,
        error: error as Error,
        message: '传统下载方式失败'
      };
    }
  }

  // 判断当前浏览器是否支持 File System Access API
  private static isFSAvailable(): boolean {
    return 'showSaveFilePicker' in window && 'showOpenFilePicker' in window && 'FileSystemFileHandle' in window;
  }

  // 确保文件名带有扩展名
  private static ensureExtension(filename: string, ext: string): string {
    return filename.endsWith(ext) ? filename : `${filename}${ext}`;
  }

  // 将内容转换为标准 Blob 对象，便于写入或下载
  private static normalizeContentToBlob(content: string | Blob | ArrayBuffer, mimeType: string): Blob {
    if (typeof content === 'string') {
      return new Blob([content], { type: mimeType }); // 文本转 Blob
    }
    if (content instanceof Blob) {
      return content; // 已是 Blob，直接返回
    }
    if (content instanceof ArrayBuffer) {
      return new Blob([new Uint8Array(content)], { type: mimeType }); // 二进制转 Blob
    }
    throw new Error('Unsupported content type'); // 不支持的类型
  }
}

export default FileSaver;
