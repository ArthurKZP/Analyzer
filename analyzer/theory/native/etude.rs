//! Le corps des études sur disque, sans perte, sous deux formes :
//!
//! - rapide (ANALYZER-ETUDE1) : gzip rapide, écrit juste après la résolution (--save, environ une seconde) ;
//! - compacte (ANALYZER-ETUDE2) : des blocs indépendants de 16 Mio compressés avec xz (LZMA2), environ 45 % plus
//!   petite. Chaque bloc : [u32 taille brute][u32 taille compressée][flux xz, avec sa somme de contrôle], puis un bloc
//!   vide pour finir. Les blocs se compressent et se relisent en parallèle, par vagues (un bloc par cœur).
//!
//! analyzer-solve --compacter etude.etude réécrit une étude rapide sous la forme compacte, vérifie la copie octet
//! par octet, puis remplace le fichier ; --load lit les deux formes.

use flate2::read::GzDecoder;
use rayon::prelude::*;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use xz2::stream::{Check, Filters, LzmaOptions, Stream};

pub const FAST_MAGIC: &[u8] = b"ANALYZER-ETUDE1\n";
pub const PACKED_MAGIC: &[u8] = b"ANALYZER-ETUDE2\n";
const CHUNK: usize = 16 << 20;
/// xz -6 (dictionnaire de 8 Mio), avec 4 bits de contexte par octet et sans alignement : le meilleur réglage mesuré
/// sur des études (les stratégies sur 8 bits n'ont pas de structure par mots).
const PRESET: u32 = 6;

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

fn pack(raw: &[u8]) -> io::Result<Vec<u8>> {
    let mut options = LzmaOptions::new_preset(PRESET).map_err(io::Error::from)?;
    options.literal_context_bits(4).position_bits(0);
    let mut filters = Filters::new();
    filters.lzma2(&options);
    let stream = Stream::new_stream_encoder(&filters, Check::Crc32).map_err(io::Error::from)?;
    let mut encoder = xz2::write::XzEncoder::new_stream(Vec::with_capacity(raw.len() / 2), stream);
    encoder.write_all(raw)?;
    encoder.finish()
}

fn unpack(raw_len: usize, data: &[u8]) -> io::Result<Vec<u8>> {
    let mut out = Vec::with_capacity(raw_len);
    xz2::read::XzDecoder::new(data).read_to_end(&mut out)?;
    if out.len() != raw_len {
        return Err(invalid("bloc d'étude tronqué"));
    }
    Ok(out)
}

/// Écrit le corps compact : les blocs pleins partent par vagues, compressés en parallèle.
pub struct PackWriter<W: Write> {
    out: W,
    buf: Vec<u8>,
    full: Vec<Vec<u8>>,
}

impl<W: Write> PackWriter<W> {
    pub fn new(out: W) -> Self {
        PackWriter { out, buf: Vec::with_capacity(CHUNK), full: Vec::new() }
    }

    fn wave(&mut self) -> io::Result<()> {
        let packed: Vec<io::Result<Vec<u8>>> = self.full.par_iter().map(|raw| pack(raw)).collect();
        for (raw, data) in self.full.drain(..).zip(packed) {
            let data = data?;
            self.out.write_all(&(raw.len() as u32).to_le_bytes())?;
            self.out.write_all(&(data.len() as u32).to_le_bytes())?;
            self.out.write_all(&data)?;
        }
        Ok(())
    }

    pub fn finish(mut self) -> io::Result<W> {
        if !self.buf.is_empty() {
            let last = std::mem::take(&mut self.buf);
            self.full.push(last);
        }
        self.wave()?;
        self.out.write_all(&[0u8; 8])?;
        Ok(self.out)
    }
}

impl<W: Write> Write for PackWriter<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let n = data.len().min(CHUNK - self.buf.len());
        self.buf.extend_from_slice(&data[..n]);
        if self.buf.len() == CHUNK {
            let full = std::mem::replace(&mut self.buf, Vec::with_capacity(CHUNK));
            self.full.push(full);
            if self.full.len() >= rayon::current_num_threads() {
                self.wave()?;
            }
        }
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Relit le corps compact, par vagues de blocs décompressés en parallèle.
pub struct PackReader<R: Read> {
    src: R,
    ready: VecDeque<Vec<u8>>,
    cur: Vec<u8>,
    pos: usize,
    done: bool,
}

impl<R: Read> PackReader<R> {
    pub fn new(src: R) -> Self {
        PackReader { src, ready: VecDeque::new(), cur: Vec::new(), pos: 0, done: false }
    }

    fn wave(&mut self) -> io::Result<()> {
        let mut packed = Vec::new();
        while !self.done && packed.len() < rayon::current_num_threads() {
            let mut head = [0u8; 8];
            self.src.read_exact(&mut head)?;
            let raw = u32::from_le_bytes([head[0], head[1], head[2], head[3]]) as usize;
            let size = u32::from_le_bytes([head[4], head[5], head[6], head[7]]) as usize;
            if raw == 0 {
                self.done = true;
            } else if raw > CHUNK || size > 2 * CHUNK {
                return Err(invalid("bloc d'étude invalide"));
            } else {
                let mut data = vec![0u8; size];
                self.src.read_exact(&mut data)?;
                packed.push((raw, data));
            }
        }
        let blocks: Vec<io::Result<Vec<u8>>> = packed.par_iter().map(|(raw, data)| unpack(*raw, data)).collect();
        for block in blocks {
            self.ready.push_back(block?);
        }
        Ok(())
    }
}

impl<R: Read> Read for PackReader<R> {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        while self.pos == self.cur.len() {
            if let Some(block) = self.ready.pop_front() {
                self.cur = block;
                self.pos = 0;
            } else if self.done {
                return Ok(0);
            } else {
                self.wave()?;
            }
        }
        let n = out.len().min(self.cur.len() - self.pos);
        out[..n].copy_from_slice(&self.cur[self.pos..self.pos + n]);
        self.pos += n;
        Ok(n)
    }
}

/// Le corps d'une étude (les stratégies sur 8 bits) selon sa forme, ou None si ce n'est pas une étude.
pub fn body<R: Read + 'static>(r: R, magic: &[u8]) -> Option<Box<dyn Read>> {
    if magic == FAST_MAGIC {
        Some(Box::new(GzDecoder::new(r)))
    } else if magic == PACKED_MAGIC {
        Some(Box::new(PackReader::new(r)))
    } else {
        None
    }
}

fn read_full(r: &mut dyn Read, buf: &mut [u8]) -> io::Result<usize> {
    let mut n = 0;
    while n < buf.len() {
        let k = r.read(&mut buf[n..])?;
        if k == 0 {
            break;
        }
        n += k;
    }
    Ok(n)
}

/// Ouvre une étude après son en-tête (forme, puis ligne JSON) : son corps.
fn open_body(path: &str) -> io::Result<Box<dyn Read>> {
    let mut r = BufReader::new(File::open(path)?);
    let mut magic = vec![0u8; FAST_MAGIC.len()];
    r.read_exact(&mut magic)?;
    let mut header = Vec::new();
    r.read_until(b'\n', &mut header)?;
    body(r, &magic).ok_or_else(|| invalid("pas une étude d'Analyzer"))
}

/// La copie compacte redonne exactement les octets de l'étude rapide, jusqu'au bloc de fin.
fn verify(fast: &str, packed: &str) -> io::Result<()> {
    let (mut a, mut b) = (open_body(fast)?, open_body(packed)?);
    let (mut x, mut y) = (vec![0u8; 1 << 20], vec![0u8; 1 << 20]);
    loop {
        let n = read_full(&mut a, &mut x)?;
        let m = read_full(&mut b, &mut y)?;
        if n != m || x[..n] != y[..m] {
            return Err(invalid("la copie compacte ne redonne pas l'étude"));
        }
        if n == 0 {
            return Ok(());
        }
    }
}

/// Réécrit une étude rapide sous la forme compacte (vérifiée), puis remplace le fichier :
/// {"avant": octets, "apres": octets, "secondes": durée}, ou {"compacte": true} si elle l'est déjà.
pub fn compact(path: &str) -> Result<Value, String> {
    let err = |e: io::Error| format!("compression de l'étude {path} : {e}");
    let start = std::time::Instant::now();
    let before = std::fs::metadata(path).map_err(err)?;
    let mut r = BufReader::new(File::open(path).map_err(err)?);
    let mut magic = vec![0u8; FAST_MAGIC.len()];
    r.read_exact(&mut magic).map_err(err)?;
    if magic == PACKED_MAGIC {
        return Ok(json!({ "compacte": true, "taille": before.len() }));
    }
    if magic != FAST_MAGIC {
        return Err(format!("{path} n'est pas une étude d'Analyzer"));
    }
    let mut header = Vec::new();
    r.read_until(b'\n', &mut header).map_err(err)?;
    let tmp = format!("{path}.compact");
    let written = (|| -> io::Result<u64> {
        let mut w = BufWriter::new(File::create(&tmp)?);
        w.write_all(PACKED_MAGIC)?;
        w.write_all(&header)?;
        let mut packer = PackWriter::new(w);
        io::copy(&mut GzDecoder::new(r), &mut packer)?;
        let mut w = packer.finish()?;
        w.flush()?;
        w.into_inner().map_err(|e| e.into_error())?.sync_all()?;
        verify(path, &tmp)?;
        Ok(std::fs::metadata(&tmp)?.len())
    })();
    let after = match written {
        Ok(size) => size,
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            return Err(err(e));
        }
    };
    // Une étude réécrite (nouvelle résolution) ou supprimée entre-temps garde sa nouvelle version.
    let unchanged = std::fs::metadata(path)
        .map(|now| now.len() == before.len() && now.modified().ok() == before.modified().ok())
        .unwrap_or(false);
    if !unchanged {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("l'étude {path} a changé pendant sa compression"));
    }
    if let Err(e) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err(e));
    }
    let seconds = (start.elapsed().as_secs_f64() * 10.0).round() / 10.0;
    Ok(json!({ "avant": before.len(), "apres": after, "secondes": seconds }))
}
