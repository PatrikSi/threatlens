#!/usr/bin/env node

import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

function parseArgs(argv) {
  const values = {
    output: null,
    legalOutputDir: null,
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
    if (current === '--legal-output-dir') {
      values.legalOutputDir = argv[index + 1] ?? null
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

function toPosixPath(value) {
  return value.split(path.sep).join(path.posix.sep)
}

function normalizePackagePath(packagePath) {
  if (packagePath.startsWith('node_modules/')) {
    return packagePath
  }

  return `node_modules/${toPosixPath(packagePath)}`
}

function resolveInstalledPackageDir(packagePath, nodeModulesDir) {
  const normalizedPackagePath = normalizePackagePath(packagePath)
  const relativePath = normalizedPackagePath.slice('node_modules/'.length)
  return path.join(nodeModulesDir, relativePath)
}

function classifyLegalFile(filename) {
  const lower = filename.toLowerCase()

  if (lower.includes('license') || lower.includes('licence')) {
    return 'license'
  }
  if (lower.includes('notice')) {
    return 'notice'
  }
  if (lower.includes('copying')) {
    return 'copying'
  }
  if (lower.includes('authors')) {
    return 'authors'
  }
  return 'other'
}

function findPackageLegalFiles(installedPackageDir) {
  if (!fs.existsSync(installedPackageDir)) {
    return []
  }

  return fs
    .readdirSync(installedPackageDir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .filter((filename) => classifyLegalFile(filename) !== 'other')
    .sort((left, right) => left.localeCompare(right))
}

function createArtifactBasePath(packageName, version) {
  const packageSegments = packageName.split('/')
  return path.posix.join(...packageSegments, version)
}

function copyPackageLegalFiles({
  packageName,
  version,
  installedPackageDir,
  normalizedPackagePath,
  legalOutputDir,
}) {
  const legalFiles = findPackageLegalFiles(installedPackageDir)

  return legalFiles.map((filename) => {
    const sourcePath = path.join(installedPackageDir, filename)
    const artifactPath = path.posix.join(createArtifactBasePath(packageName, version), filename)
    const contents = fs.readFileSync(sourcePath)
    const sha256 = createHash('sha256').update(contents).digest('hex')

    if (legalOutputDir) {
      const targetPath = path.join(legalOutputDir, artifactPath)
      fs.mkdirSync(path.dirname(targetPath), { recursive: true })
      fs.writeFileSync(targetPath, contents)
    }

    return {
      kind: classifyLegalFile(filename),
      file_name: filename,
      source_path: path.posix.join(normalizedPackagePath, filename),
      artifact_path: artifactPath,
      sha256,
    }
  })
}

function collectRuntimePackageMetadata({ lockfile, nodeModulesDir, legalOutputDir }) {
  const lock = JSON.parse(fs.readFileSync(lockfile, 'utf8'))
  const packages = lock.packages || {}
  const rows = []
  const missingLegalFiles = []

  for (const [packagePath, packageMeta] of Object.entries(packages)) {
    if (!packagePath.startsWith('node_modules/')) continue
    if (packageMeta.dev) continue

    const installedPackageDir = resolveInstalledPackageDir(packagePath, nodeModulesDir)
    const manifestPath = path.join(installedPackageDir, 'package.json')
    if (!fs.existsSync(manifestPath)) {
      continue
    }

    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    const fallbackName = packagePath.slice('node_modules/'.length)
    const name = (manifest.name || packageMeta.name || fallbackName || '').trim()
    const version = (manifest.version || packageMeta.version || '').trim()
    if (!name || !version) continue

    const normalizedPackagePath = normalizePackagePath(packagePath)
    const redistributionFiles = copyPackageLegalFiles({
      packageName: name,
      version,
      installedPackageDir,
      normalizedPackagePath,
      legalOutputDir,
    })

    if (legalOutputDir && redistributionFiles.length === 0) {
      missingLegalFiles.push(`${name}@${version} (${normalizedPackagePath})`)
    }

    const row = {
      name,
      version,
      license: manifest.license || packageMeta.license || 'Unknown',
      description: manifest.description || null,
      homepage: manifest.homepage || null,
      repository: normalizeRepository(manifest.repository),
      author: normalizeAuthor(manifest.author),
      package_path: normalizedPackagePath,
      license_files: redistributionFiles
        .filter((file) => file.kind === 'license')
        .map((file) => file.file_name),
      notice_files: redistributionFiles
        .filter((file) => file.kind === 'notice')
        .map((file) => file.file_name),
      redistribution_files: redistributionFiles,
    }

    rows.push(row)
  }

  if (legalOutputDir && missingLegalFiles.length > 0) {
    throw new Error(
      `Missing package-published legal files for runtime dependencies: ${missingLegalFiles.join(', ')}`,
    )
  }

  rows.sort((left, right) => left.name.localeCompare(right.name) || left.version.localeCompare(right.version))
  return rows
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  if (args.legalOutputDir) {
    fs.rmSync(args.legalOutputDir, { recursive: true, force: true })
    fs.mkdirSync(args.legalOutputDir, { recursive: true })
  }
  const metadata = collectRuntimePackageMetadata({
    lockfile: args.lockfile,
    nodeModulesDir: args.nodeModulesDir,
    legalOutputDir: args.legalOutputDir,
  })
  fs.writeFileSync(args.output, `${JSON.stringify(metadata, null, 2)}\n`)
}

main()
