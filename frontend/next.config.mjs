/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    // dev 代理默认 30s 就掐断转发请求（proxy-request.js 的 proxyTimeout || 30000），
    // 而语音克隆要 30s~2min，会被误断成 500。设为 10 分钟，远超最坏生成耗时。
    proxyTimeout: 600000,
  },
  async rewrites() {
    // 局域网部署形态：浏览器只访问 3000 端口，/api/*（含音频文件 /api/files/*）
    // 由 Next 同源代理转发到本机 7871。后端无论被以何种 host 参数启动
    // （127.0.0.1 或 0.0.0.0）、CORS 如何配置，都不影响局域网同事使用。
    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:7871/api/:path*',
      },
    ]
  },
}

export default nextConfig
