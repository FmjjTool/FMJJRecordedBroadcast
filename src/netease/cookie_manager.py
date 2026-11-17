"""Cookie管理器模块

提供网易云音乐Cookie管理功能，包括：
- Cookie文件读取和写入
- Cookie格式验证和解析
- Cookie有效性检查
- 自动过期处理
"""

from typing import Dict, Any
from pathlib import Path
from datetime import datetime
import logging


class CookieException(Exception):
    """Cookie相关异常类"""
    pass


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """解析 Cookie 字符串为字典（纯内存，不读写任何文件）。"""
    if not cookie_string or not cookie_string.strip():
        return {}

    text = cookie_string.strip()
    if ';' in text:
        pairs = text.split(';')
    elif '\n' in text:
        pairs = text.split('\n')
    else:
        pairs = [text]

    cookies = {}
    for pair in pairs:
        pair = pair.strip()
        if not pair or '=' not in pair:
            continue
        key, value = pair.split('=', 1)
        key, value = key.strip(), value.strip()
        if key and value:
            cookies[key] = value
    return cookies


class CookieManager:
    """Cookie管理器主类"""
    
    def __init__(self, cookie_file: str = "cookie.txt"):
        """
        初始化Cookie管理器
        
        Args:
            cookie_file: Cookie文件路径
        """
        self.cookie_file = Path(cookie_file)
        self.logger = logging.getLogger(__name__)
        
        # 网易云音乐相关的重要Cookie字段
        self.important_cookies = {
            'MUSIC_U',      # 用户标识
            'MUSIC_A',      # 用户认证
            '__csrf',       # CSRF令牌
            'NMTID',        # 设备标识
            'WEVNSM',       # 会话管理
            'WNMCID',       # 客户端标识
        }
        
    
    
    def read_cookie(self) -> str:
        """读取Cookie文件内容
        
        Returns:
            Cookie字符串内容
            
        Raises:
            CookieException: 读取失败时抛出
        """
        try:
            if not self.cookie_file.exists():
                self.logger.warning(f"Cookie文件不存在: {self.cookie_file}")
                return ""
            
            content = self.cookie_file.read_text(encoding='utf-8').strip()
            
            if not content:
                self.logger.warning("Cookie文件为空")
                return ""
            
            self.logger.debug(f"成功读取Cookie文件，长度: {len(content)}")
            return content
            
        except UnicodeDecodeError as e:
            raise CookieException(f"Cookie文件编码错误: {e}")
        except PermissionError as e:
            raise CookieException(f"没有权限读取Cookie文件: {e}")
        except Exception as e:
            raise CookieException(f"读取Cookie文件失败: {e}")
    
    def parse_cookies(self) -> Dict[str, str]:
        """解析Cookie字符串为字典
        
        Returns:
            Cookie字典
            
        Raises:
            CookieException: 解析失败时抛出
        """
        try:
            cookie_content = self.read_cookie()
            if not cookie_content:
                return {}
            
            return self.parse_cookie_string(cookie_content)
            
        except Exception as e:
            raise CookieException(f"解析Cookie失败: {e}")
    
    def parse_cookie_string(self, cookie_string: str) -> Dict[str, str]:
        """解析Cookie字符串（纯内存，不访问文件）。"""
        return parse_cookie_string(cookie_string)
    
    def is_cookie_valid(self) -> bool:
        """检查Cookie是否有效
        
        Returns:
            Cookie是否有效
        """
        try:
            cookies = self.parse_cookies()
            
            if not cookies:
                self.logger.warning("Cookie为空")
                return False
            
            # 检查重要Cookie是否存在
            missing_cookies = self.important_cookies - set(cookies.keys())
            if missing_cookies:
                self.logger.warning(f"缺少重要Cookie: {missing_cookies}")
                return False
            
            # 检查MUSIC_U是否有效（基本验证）
            music_u = cookies.get('MUSIC_U', '')
            if not music_u or len(music_u) < 10:
                self.logger.warning("MUSIC_U Cookie无效")
                return False
            
            self.logger.debug("Cookie验证通过")
            return True
            
        except Exception as e:
            self.logger.error(f"Cookie验证失败: {e}")
            return False
    
    def get_cookie_info(self) -> Dict[str, Any]:
        """获取Cookie详细信息
        
        Returns:
            包含Cookie信息的字典
        """
        try:
            cookies = self.parse_cookies()
            
            info = {
                'file_path': str(self.cookie_file),
                'file_exists': self.cookie_file.exists(),
                'file_size': self.cookie_file.stat().st_size if self.cookie_file.exists() else 0,
                'cookie_count': len(cookies),
                'is_valid': self.is_cookie_valid(),
                'important_cookies_present': list(self.important_cookies & set(cookies.keys())),
                'missing_important_cookies': list(self.important_cookies - set(cookies.keys())),
                'all_cookie_names': list(cookies.keys())
            }
            
            # 添加文件修改时间
            if self.cookie_file.exists():
                mtime = self.cookie_file.stat().st_mtime
                info['last_modified'] = datetime.fromtimestamp(mtime).isoformat()
            
            return info
            
        except Exception as e:
            return {
                'error': str(e),
                'file_path': str(self.cookie_file),
                'file_exists': False,
                'is_valid': False
            }
    
    
    
    def __str__(self) -> str:
        """字符串表示"""
        info = self.get_cookie_info()
        return f"CookieManager(file={info['file_path']}, valid={info['is_valid']}, count={info['cookie_count']})"
    
    def __repr__(self) -> str:
        """详细字符串表示"""
        return self.__str__()


if __name__ == "__main__":
    # 测试代码
    
    # 配置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    manager = CookieManager()
    
    print("Cookie管理器模块")
    print("支持的功能:")
    print("- Cookie文件读写")
    print("- Cookie格式验证")
    print("- Cookie有效性检查")
    print("- Cookie备份和恢复")
    print("- Cookie信息查看")
    
    # 显示当前Cookie信息
    info = manager.get_cookie_info()
    print(f"\n当前Cookie状态: {manager}")
    print(f"详细信息: {info}")
