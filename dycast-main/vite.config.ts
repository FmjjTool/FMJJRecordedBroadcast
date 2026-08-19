import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import configPlugin from './src/plugins/configPlugin';

const REDIRECT_RULES = [
  {
    host: /^(https?:)?\/\/live\.douyin\.com/i,
    prefix: '/dylive'
  },
  {
    host: /^(https?:)?\/\/www\.douyin\.com/i,
    prefix: '/dywww'
  },
  {
    host: /^(https?:)?\/\/m\.douyin\.com/i,
    prefix: '/dym'
  },
  {
    host: /^(https?:)?\/\/webcast\.amemv\.com/i,
    prefix: '/dywebcast'
  }
];

const rewriteRedirectLocation = function (loc: string, fallbackPrefix: string) {
  if (!loc) return loc;
  for (const rule of REDIRECT_RULES) {
    if (rule.host.test(loc)) {
      return loc.replace(rule.host, rule.prefix);
    }
  }
  if (loc.startsWith('/')) return `${fallbackPrefix}${loc}`;
  return loc;
};

const createDouyinPageProxy = function (prefix: string, target: string, referer: string) {
  return {
    target,
    changeOrigin: true,
    rewrite: (pathname: string) => pathname.replace(new RegExp(`^${prefix}`), ''),
    configure: (proxy: any) => {
      proxy.on('proxyReq', (proxyReq: any, req: any) => {
        const ua = req.headers['user-agent'] || '';
        const isMobile = /mobile|android|iphone|ipad/i.test(ua);
        const isPreviewReq = (req.url || '').includes('dycast_preview=1');
        const desktopUA =
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0';
        if (isMobile) {
          proxyReq.setHeader(
            'User-Agent',
            desktopUA
          );
        }
        if (isPreviewReq) {
          // 预览优先走桌面站，避免跳转到 reflow 页面导致白屏/拒绝连接
          proxyReq.setHeader('User-Agent', desktopUA);
        }
        proxyReq.setHeader('Referer', referer);
      });

      proxy.on('proxyRes', (proxyRes: any) => {
        const setCookie = proxyRes.headers['set-cookie'];
        if (setCookie) {
          // 始终允许抖音返回的 set-cookie 通过（__ac_nonce 等必须保留）
          // 仅清理 Domain / SameSite / Secure 属性以适配本地代理
          const newCookie = setCookie.map((cookie: string) =>
            cookie
              .replace(/; Domain=[^;]+/i, '')
              .replace(/; SameSite=None/i, '')
              .replace(/; Secure=true/i, '')
          );
          proxyRes.headers['set-cookie'] = newCookie;
        }
        const location = proxyRes.headers['location'];
        if (typeof location === 'string') {
          proxyRes.headers['location'] = rewriteRedirectLocation(location, prefix);
        } else if (Array.isArray(location)) {
          proxyRes.headers['location'] = location.map((loc: string) => rewriteRedirectLocation(loc, prefix));
        }
        delete proxyRes.headers['x-frame-options'];
        delete proxyRes.headers['content-security-policy'];
        delete proxyRes.headers['content-security-policy-report-only'];
      });
    }
  };
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    configPlugin()
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: '0.0.0.0', // 允许局域网访问
    open: true, // 启动后自动打开浏览器
    hmr: {
      // 为 HMR 指定独立的 WebSocket 路径，避免与 /socket 代理冲突
      path: 'hmr',
      overlay: true
    },
    proxy: {
      '/dylive': createDouyinPageProxy('/dylive', 'https://live.douyin.com', 'https://live.douyin.com/'),
      '/dywww': createDouyinPageProxy('/dywww', 'https://www.douyin.com', 'https://www.douyin.com/'),
      '/dym': createDouyinPageProxy('/dym', 'https://m.douyin.com', 'https://m.douyin.com/'),
      '/dywebcast': createDouyinPageProxy('/dywebcast', 'https://webcast.amemv.com', 'https://live.douyin.com/'),
      '/socket': {
        target: 'wss://webcast5-ws-web-lf.douyin.com',
        changeOrigin: true, // 保持原始 Host，利于服务端识别 Cookie
        secure: false,
        ws: true, // 启用 WebSocket 代理
        rewrite: path => path.replace(/^\/socket/, ''),
        configure: proxy => {
          proxy.on('proxyReqWs', (proxyReq, req) => {
            const ua = req.headers['user-agent'] || '';
            const isMobile = /mobile|android|iphone|ipad/i.test(ua);
            // 这里可以不设置也
            if (isMobile) {
              // 设置请求头 User-Agent 标识
              proxyReq.setHeader(
                'User-Agent',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0'
              );
            }
            // 注意：不要在 WebSocket 握手时注入用户 Cookie
            // 注入自定义 Cookie 会导致抖音弹幕 WebSocket 服务器拒绝连接或无法正常推送数据
            // Cookie 仅用于 HTTP 请求（获取房间信息/直播流）
          });
        }
      }
    }
  }
});
