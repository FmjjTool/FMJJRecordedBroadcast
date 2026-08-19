import { Plugin } from 'vite';
import fs from 'node:fs';
import path from 'node:path';
import { exec } from 'node:child_process';
import https from 'node:https';
import http from 'node:http';

const CONFIG_FILE = 'dycast.config.json';
const SESSION_DIR = 'sessions';
const SESSION_RESTORE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

const defaultConfig = {
    monitorInterval: 30000,
    statsInterval: 3000,
    monitorRoomList: '',
    douyinCookie: '',
    enableRecording: false,
    darkMode: false,
    forceLatestScroll: false
};

/** 读取 JSON body */
function readBody(req: any): Promise<string> {
    return new Promise((resolve) => {
        let body = '';
        req.on('data', (chunk: string) => (body += chunk));
        req.on('end', () => resolve(body));
    });
}

/** 格式化日期 yyyyMMdd_HHmmss */
function formatDateStr(d: Date): string {
    const pad = (n: number) => n.toString().padStart(2, '0');
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/**
 * Vite 插件：提供本地文件读写 API
 *  /api/config    GET/POST        → 读写配置
 *  /api/session   GET/POST/DELETE → 读写/清除会话数据（按主播+房间+日期分文件）
 *  /api/heartbeat POST            → 前端心跳
 */
export default function configPlugin(): Plugin {
    let configPath = '';
    let sessionDir = '';

    // 心跳机制
    let lastHeartbeat = 0;
    let heartbeatChecker: ReturnType<typeof setInterval> | null = null;
    let serverPort = 5173;
    let browserOpening = false;

    /** 确保 sessions 目录存在 */
    function ensureSessionDir() {
        if (!fs.existsSync(sessionDir)) {
            fs.mkdirSync(sessionDir, { recursive: true });
        }
    }

    /** 根据会话数据生成文件名 */
    function getSessionFileName(session: any): string {
        const nick = session.nickname || '未知主播';
        const room = session.roomId || '000';
        const startTime = session.startTime || formatDateStr(new Date());
        // 文件名不能有特殊字符
        const safeName = `${nick}-${room}-${startTime}`.replace(/[\\/:*?"<>|]/g, '_');
        return `${safeName}.session.json`;
    }

    /** 根据会话数据生成封面文件名 */
    function getCoverFileName(session: any): string {
        const nick = session.nickname || '未知主播';
        const room = session.roomId || '000';
        const startTime = session.startTime || formatDateStr(new Date());
        const safeName = `${nick}-${room}-${startTime}`.replace(/[\\/:*?"<>|]/g, '_');
        return `${safeName}-cover.jpg`;
    }

    /** 下载封面图片到 sessions 目录（仅首次下载） */
    function downloadCover(coverUrl: string, destPath: string) {
        if (fs.existsSync(destPath)) return; // 已下载过
        if (!coverUrl || coverUrl.startsWith('data:') || coverUrl.startsWith('blob:')) return;

        const client = coverUrl.startsWith('https') ? https : http;
        try {
            client.get(coverUrl, (response) => {
                if (response.statusCode === 200) {
                    const file = fs.createWriteStream(destPath);
                    response.pipe(file);
                    file.on('finish', () => {
                        file.close();
                        console.log(`[session] 封面已保存: ${path.basename(destPath)}`);
                    });
                } else if (response.statusCode === 301 || response.statusCode === 302) {
                    // 处理重定向
                    const redirect = response.headers.location;
                    if (redirect) downloadCover(redirect, destPath);
                }
            }).on('error', (e) => {
                console.error('[session] 封面下载失败:', e.message);
            });
        } catch (e) {
            console.error('[session] 封面下载出错:', e);
        }
    }

    /** 获取所有会话文件，按修改时间降序 */
    function getAllSessionFiles(): { path: string; mtime: number }[] {
        ensureSessionDir();
        try {
            const files = fs.readdirSync(sessionDir)
                .filter(f => f.endsWith('.session.json'))
                .map(f => {
                    const fp = path.join(sessionDir, f);
                    const stat = fs.statSync(fp);
                    return { path: fp, mtime: stat.mtimeMs };
                })
                .sort((a, b) => b.mtime - a.mtime);
            return files;
        } catch {
            return [];
        }
    }

    /** 获取会话保存时间（优先 savedAt，兜底文件修改时间） */
    function getSessionSavedAtMs(session: any, filePath: string): number | null {
        if (session?.savedAt) {
            const savedAtMs = Date.parse(session.savedAt);
            if (!Number.isNaN(savedAtMs)) return savedAtMs;
        }
        try {
            if (fs.existsSync(filePath)) {
                const stat = fs.statSync(filePath);
                return stat.mtimeMs;
            }
        } catch { }
        return null;
    }

    /** 找到最新的有效会话 */
    function findLatestSession(): { data: any; filePath: string } | null {
        const files = getAllSessionFiles();
        const now = Date.now();
        for (const f of files) {
            try {
                const raw = fs.readFileSync(f.path, 'utf-8');
                const session = JSON.parse(raw);
                if (!session || !session.casts || session.casts.length <= 0) continue;
                const sessionSavedAt = getSessionSavedAtMs(session, f.path);
                if (sessionSavedAt == null) continue;
                if (now - sessionSavedAt > SESSION_RESTORE_MAX_AGE_MS) continue;
                if (session && session.casts && session.casts.length > 0) {
                    return { data: session, filePath: f.path };
                }
            } catch { }
        }
        return null;
    }

    return {
        name: 'dycast-config',
        configResolved(config) {
            configPath = path.resolve(config.root, CONFIG_FILE);
            sessionDir = path.resolve(config.root, SESSION_DIR);
            ensureSessionDir();
        },
        configureServer(server) {
            server.httpServer?.once('listening', () => {
                const addr = server.httpServer?.address();
                if (addr && typeof addr === 'object') {
                    serverPort = addr.port;
                }

                // 启动时检测：如果有残留的会话数据，自动打开浏览器恢复
                setTimeout(() => {
                    const latest = findLatestSession();
                    if (latest) {
                        const url = `http://localhost:${serverPort}`;
                        const viteAutoOpen = Boolean(server.config.server.open);
                        if (viteAutoOpen) {
                            console.log(`[startup] 检测到会话数据：${path.basename(latest.filePath)}（${latest.data.casts.length} 条），Vite 已自动打开浏览器，跳过二次打开`);
                        } else {
                            console.log(`[startup] 检测到会话数据：${path.basename(latest.filePath)}（${latest.data.casts.length} 条），自动打开浏览器恢复...`);
                            exec(`start ${url}`);
                            browserOpening = true;
                            setTimeout(() => { browserOpening = false; }, 30000);
                        }
                    }
                }, 2000);

                // 心跳检测延迟 60 秒启动（避免 HMR 重载时误触发）
                // 考虑到浏览器最小化会导致定时器变慢引起误报，暂时停用心跳自动重开功能
                // 仅保留启动时的自动恢复检测
                /*
                setTimeout(() => {
                    heartbeatChecker = setInterval(() => {
                        const now = Date.now();
                        const elapsed = now - lastHeartbeat;

                        // 超过 30 秒没有心跳，且非启动/刷新阶段
                        if (lastHeartbeat > 0 && elapsed > 30000 && !browserOpening) {
                            const latest = findLatestSession();
                            if (latest) {
                                browserOpening = true;
                                const url = `http://localhost:${serverPort}`;
                                console.log(`[heartbeat] 检测到页面关闭（${Math.round(elapsed / 1000)}秒无心跳），正在自动恢复: ${url}`);
                                exec(`start ${url}`, (err) => {
                                    if (err) console.error('[heartbeat] 打开浏览器失败:', err);
                                    setTimeout(() => { browserOpening = false; }, 60000);
                                });
                            }
                        }
                    }, 15000);
                }, 60000);
                */
            });

            // === 配置接口 ===
            server.middlewares.use('/api/config', (req, res) => {
                if (req.method === 'GET') {
                    let data = { ...defaultConfig };
                    try {
                        if (fs.existsSync(configPath)) {
                            const raw = fs.readFileSync(configPath, 'utf-8');
                            data = { ...defaultConfig, ...JSON.parse(raw) };
                        }
                    } catch (e) {
                        console.error('[config] 读取配置失败:', e);
                    }
                    res.setHeader('Content-Type', 'application/json');
                    res.end(JSON.stringify(data));
                } else if (req.method === 'POST') {
                    readBody(req).then(body => {
                        try {
                            const newConfig = JSON.parse(body);
                            fs.writeFileSync(configPath, JSON.stringify(newConfig, null, 2), 'utf-8');
                            res.setHeader('Content-Type', 'application/json');
                            res.end(JSON.stringify({ success: true }));
                        } catch (e) {
                            console.error('[config] 保存配置失败:', e);
                            res.statusCode = 500;
                            res.end(JSON.stringify({ success: false, message: String(e) }));
                        }
                    });
                } else {
                    res.statusCode = 405;
                    res.end('Method Not Allowed');
                }
            });

            // === 心跳接口 ===
            server.middlewares.use('/api/heartbeat', (req, res) => {
                lastHeartbeat = Date.now();
                browserOpening = false;
                res.setHeader('Content-Type', 'application/json');
                res.end(JSON.stringify({ ok: true }));
            });

            // === 会话接口 ===
            // GET  /api/session        → 返回最新会话
            // GET  /api/session?list=1  → 返回所有会话文件列表
            // POST /api/session         → 保存会话（按主播+房间+日期分文件）
            // DELETE /api/session       → 删除当前会话文件
            server.middlewares.use('/api/session', (req, res) => {
                const url = new URL(req.url || '', `http://localhost:${serverPort}`);

                if (req.method === 'GET') {
                    if (url.searchParams.get('list') === '1') {
                        // 返回所有会话文件列表
                        const files = getAllSessionFiles();
                        const list = files.map(f => {
                            try {
                                const raw = fs.readFileSync(f.path, 'utf-8');
                                const s = JSON.parse(raw);
                                return {
                                    file: path.basename(f.path),
                                    nickname: s.nickname || '未知',
                                    roomId: s.roomId || '',
                                    castCount: s.casts?.length || 0,
                                    savedAt: s.savedAt || '',
                                    startTime: s.startTime || ''
                                };
                            } catch {
                                return { file: path.basename(f.path), nickname: '损坏', castCount: 0 };
                            }
                        });
                        res.setHeader('Content-Type', 'application/json');
                        res.end(JSON.stringify(list));
                    } else {
                        // 返回最新会话
                        // 返回最新会话（替换封面为本地路径）
                        const latest = findLatestSession();
                        if (latest) {
                            const coverName = getCoverFileName(latest.data);
                            const coverPath = path.join(sessionDir, coverName);
                            if (fs.existsSync(coverPath)) {
                                latest.data.cover = `/api/sessions-img/${coverName}`;
                            }
                        }
                        res.setHeader('Content-Type', 'application/json');
                        res.end(latest ? JSON.stringify(latest.data) : JSON.stringify(null));
                    }
                } else if (req.method === 'POST') {
                    readBody(req).then(body => {
                        try {
                            ensureSessionDir();
                            const session = JSON.parse(body);
                            const fileName = getSessionFileName(session);
                            const filePath = path.join(sessionDir, fileName);
                            fs.writeFileSync(filePath, body, 'utf-8');

                            // 自动下载封面图片
                            if (session.cover && !session.cover.startsWith('data:') && !session.cover.startsWith('blob:') && !session.cover.startsWith('/api/')) {
                                const coverName = getCoverFileName(session);
                                const coverPath = path.join(sessionDir, coverName);
                                downloadCover(session.cover, coverPath);
                            }

                            res.setHeader('Content-Type', 'application/json');
                            res.end(JSON.stringify({ success: true, file: fileName }));
                        } catch (e) {
                            console.error('[session] 保存会话失败:', e);
                            res.statusCode = 500;
                            res.end(JSON.stringify({ success: false, message: String(e) }));
                        }
                    });
                } else if (req.method === 'DELETE') {
                    try {
                        // 删除当前（最新）会话
                        const latest = findLatestSession();
                        if (latest) {
                            // 删除 JSON 文件
                            if (fs.existsSync(latest.filePath)) {
                                fs.unlinkSync(latest.filePath);
                                console.log(`[session] 已删除会话数据: ${path.basename(latest.filePath)}`);
                            }

                            // 删除对应的封面图片
                            const coverName = getCoverFileName(latest.data);
                            const coverPath = path.join(sessionDir, coverName);
                            if (fs.existsSync(coverPath)) {
                                fs.unlinkSync(coverPath);
                                console.log(`[session] 已删除对应封面: ${coverName}`);
                            }
                        }
                        lastHeartbeat = 0;
                        res.setHeader('Content-Type', 'application/json');
                        res.end(JSON.stringify({ success: true }));
                    } catch (e) {
                        console.error('[session] 清除会话失败:', e);
                        res.statusCode = 500;
                        res.end(JSON.stringify({ success: false, message: String(e) }));
                    }
                } else {
                    res.statusCode = 405;
                    res.end('Method Not Allowed');
                }
            });

            // === 封面图片静态服务 ===
            server.middlewares.use('/api/sessions-img/', (req, res) => {
                const fileName = decodeURIComponent((req.url || '').replace(/^\//, '').split('?')[0]);
                if (!fileName) {
                    res.statusCode = 404;
                    res.end('Not Found');
                    return;
                }
                const filePath = path.join(sessionDir, fileName);
                if (fs.existsSync(filePath)) {
                    const ext = path.extname(filePath).toLowerCase();
                    const mimeMap: Record<string, string> = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'
                    };
                    res.setHeader('Content-Type', mimeMap[ext] || 'image/jpeg');
                    res.setHeader('Cache-Control', 'max-age=86400');
                    fs.createReadStream(filePath).pipe(res);
                } else {
                    res.statusCode = 404;
                    res.end('Not Found');
                }
            });

            // === 直接保存文件到本地（绕过浏览器下载限制） ===
            // POST /api/save-file  body: { name: string, content: string (base64), mimeType?: string }
            server.middlewares.use('/api/save-file', (req, res) => {
                if (req.method !== 'POST') {
                    res.statusCode = 405;
                    res.end('Method Not Allowed');
                    return;
                }
                readBody(req).then(body => {
                    try {
                        const { name, content } = JSON.parse(body);
                        if (!name || !content) {
                            res.statusCode = 400;
                            res.end(JSON.stringify({ success: false, message: '缺少 name 或 content' }));
                            return;
                        }
                        // 保存到项目根目录下的 downloads 文件夹
                        const downloadDir = path.resolve(configPath, '..', 'downloads');
                        if (!fs.existsSync(downloadDir)) {
                            fs.mkdirSync(downloadDir, { recursive: true });
                        }
                        // 安全文件名
                        const safeName = name.replace(/[\\/:*?"<>|]/g, '_');
                        const filePath = path.join(downloadDir, safeName);
                        // content 为 base64 编码
                        const buffer = Buffer.from(content, 'base64');
                        fs.writeFileSync(filePath, buffer);
                        console.log(`[save-file] 文件已保存: ${filePath} (${Math.round(buffer.length / 1024)}KB)`);
                        res.setHeader('Content-Type', 'application/json');
                        res.end(JSON.stringify({ success: true, path: filePath, size: buffer.length }));
                    } catch (e) {
                        console.error('[save-file] 保存文件失败:', e);
                        res.statusCode = 500;
                        res.end(JSON.stringify({ success: false, message: String(e) }));
                    }
                });
            });
        }
    };
}
