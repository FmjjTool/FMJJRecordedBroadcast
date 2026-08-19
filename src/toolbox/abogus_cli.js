// 抖音 a_bogus 签名 CLI（供 Python subprocess 调用）
// 用法: node abogus_cli.js "<query>" "<user_agent>"
const fs = require('fs');
const path = require('path');
const code = fs.readFileSync(path.join(__dirname, 'a_bogus.js'), 'utf-8');
eval(code);
const query = process.argv[2] || '';
const ua = process.argv[3] || '';
console.log(generate_a_bogus(query, ua));
