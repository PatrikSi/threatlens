#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'

function parseArgs(argv) {
  const values = {
    output: null,
    lockfile: 'package-lock.json',
    nodeModulesDir: 'node_modules',
  }

  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index]
    if (current === '--output') {
      values.output = argv[index + 1] ?? null
      index += 1
      continue
    }
    if (current === '--lockfile') {
      values.lockfile = argv[index + 1] ?? values.lockfile
      index += 1
      continue
    }
    if (current === '--node-modules-dir') {
      values.nodeModulesDir = argv[index + 1] ?? values.nodeModulesDir
      index += 1
    }
  }

  if (!values.output) {
    throw new Error('Missing required --output argument')
  }

  return values
}

function normalizeRepository(value) {
  if (!value) return null
  if (typeof value === 'string') return value
  if (typeof value === 'object' && typeof value.url === 'string') {
    return value.url
  }
  return null
}

function normalizeAuthor(value) {
  if (!value) return null
  if (typeof value === 'string') return value
  if (typeof value === 'object' && typeof value.name === 'string') {
    return value.name
  }
  return null
}

function collectRuntimePackageMetadata({ lockfile, nodeModulesDir }) {
  const lock = JSON.parse(fs.readFileSync(lockfile, 'utf8'))
  const packages = lock.packages || {}
  const rows = []

  for (const [packagePath, packageMeta] of Object.entries(packages)) {
    if (!packagePath.startsWith('node_modules/')) continue
    if (packageMeta.dev) continue

    const manifestPath = path.join(packagePath, 'package.json')
    if (!fs.existsSync(manifestPath)) {
      continue
    }

    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    const fallbackName = packagePath.slice('node_modules/'.length)
    const name = (manifest.name || packageMeta.name || fallbackName || '').trim()
    const version = (manifest.version || packageMeta.version || '').trim()
    if (!name || !version) continue

    const row = {
      name,
      version,
      license: manifest.license || packageMeta.license || 'Unknown',
      description: manifest.description || null,
      homepage: manifest.homepage || null,
      repository: normalizeRepository(manifest.repository),
      author: normalizeAuthor(manifest.author),
      package_path: packagePath.replace(`${nodeModulesDir}/`, 'node_modules/'),
    }

    rows.push(row)
  }

  rows.sort((left, right) => left.name.localeCompare(right.name) || left.version.localeCompare(right.version))
  return rows
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  const metadata = collectRuntimePackageMetadata({
    lockfile: args.lockfile,
    nodeModulesDir: args.nodeModulesDir,
  })
  fs.writeFileSync(args.output, `${JSON.stringify(metadata, null, 2)}\n`)
}

main()
