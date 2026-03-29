package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"

	mtpx "github.com/ganeshrvel/go-mtpx"
	"github.com/ganeshrvel/go-mtpfs/mtp"
)

type request struct {
	ID     int                    `json:"id"`
	Method string                 `json:"method"`
	Params map[string]interface{} `json:"params"`
}

type response struct {
	ID     int         `json:"id"`
	OK     bool        `json:"ok"`
	Result interface{} `json:"result,omitempty"`
	Error  string      `json:"error,omitempty"`
}

type fileRecord struct {
	ID    uint32 `json:"id"`
	Path  string `json:"path"`
	Name  string `json:"name"`
	Size  int64  `json:"size"`
	IsDir bool   `json:"is_dir"`
}

type bridgeState struct {
	dev *mtp.Device
	sid uint32
}

func (s *bridgeState) ensureConnected() error {
	if s.dev != nil {
		return nil
	}

	dev, err := mtpx.Initialize(mtpx.Init{DebugMode: false})
	if err != nil {
		return err
	}

	storages, err := mtpx.FetchStorages(dev)
	if err != nil {
		mtpx.Dispose(dev)
		return err
	}
	if len(storages) == 0 {
		mtpx.Dispose(dev)
		return fmt.Errorf("no MTP storage found")
	}

	s.dev = dev
	s.sid = storages[0].Sid
	return nil
}

func (s *bridgeState) close() {
	if s.dev != nil {
		mtpx.Dispose(s.dev)
		s.dev = nil
		s.sid = 0
	}
}

func main() {
	state := &bridgeState{}
	defer state.close()

	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)

	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			if errors.Is(err, io.EOF) {
				return
			}
			writeResponse(writer, response{ID: 0, OK: false, Error: err.Error()})
			continue
		}

		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		var req request
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			writeResponse(writer, response{ID: 0, OK: false, Error: err.Error()})
			continue
		}

		res := handleRequest(state, req)
		writeResponse(writer, res)

		if req.Method == "close" {
			return
		}
	}
}

func writeResponse(writer *bufio.Writer, res response) {
	encoded, err := json.Marshal(res)
	if err != nil {
		fallback := fmt.Sprintf(`{"id":%d,"ok":false,"error":%q}`, res.ID, err.Error())
		_, _ = writer.WriteString(fallback + "\n")
		_ = writer.Flush()
		return
	}
	_, _ = writer.WriteString(string(encoded) + "\n")
	_ = writer.Flush()
}

func handleRequest(state *bridgeState, req request) response {
	switch req.Method {
	case "close":
		state.close()
		return response{ID: req.ID, OK: true, Result: map[string]bool{"closed": true}}
	case "detect":
		if err := state.ensureConnected(); err != nil {
			state.close()
			return response{ID: req.ID, OK: true, Result: map[string]bool{"detected": false}}
		}
		return response{ID: req.ID, OK: true, Result: map[string]bool{"detected": true}}
	case "list":
		return handleList(state, req)
	case "download":
		return handleDownload(state, req)
	case "upload":
		return handleUpload(state, req)
	case "delete":
		return handleDelete(state, req)
	default:
		return response{ID: req.ID, OK: false, Error: "unsupported method"}
	}
}

func handleList(state *bridgeState, req request) response {
	if err := state.ensureConnected(); err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	basePath := getParamString(req.Params, "base_path", "/")
	basePath = normalizePath(basePath)

	records := []fileRecord{}
	listErr := walkBase(state, basePath, func(fi *mtpx.FileInfo, fullPath string) error {
		records = append(records, fileRecord{
			ID:    fi.ObjectId,
			Path:  fullPath,
			Name:  fi.Name,
			Size:  fi.Size,
			IsDir: fi.IsDir,
		})
		return nil
	})
	if listErr != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: listErr.Error()}
	}

	return response{
		ID: req.ID,
		OK: true,
		Result: map[string]interface{}{
			"base_path": basePath,
			"files":     records,
		},
	}
}

func handleDownload(state *bridgeState, req request) response {
	if err := state.ensureConnected(); err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	basePath := getParamString(req.Params, "base_path", "/documents")
	remotePath := getParamString(req.Params, "path", "")
	destination := getParamString(req.Params, "destination", "")
	if remotePath == "" || destination == "" {
		return response{ID: req.ID, OK: false, Error: "path and destination are required"}
	}

	fullRemotePath := resolvePath(basePath, remotePath)
	tmpDir, err := os.MkdirTemp("", "hearth-mtpx-download-")
	if err != nil {
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}
	defer os.RemoveAll(tmpDir)

	_, _, err = mtpx.DownloadFiles(
		state.dev,
		state.sid,
		[]string{fullRemotePath},
		tmpDir,
		true,
		func(_ *mtpx.FileInfo, cbErr error) error {
			return cbErr
		},
		func(_ *mtpx.ProgressInfo, cbErr error) error {
			return cbErr
		},
	)
	if err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	downloadedPath, err := locateDownloadedFile(tmpDir, fullRemotePath)
	if err != nil {
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	if err := copyFile(downloadedPath, destination); err != nil {
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	return response{ID: req.ID, OK: true, Result: map[string]string{"destination": destination}}
}

func handleUpload(state *bridgeState, req request) response {
	if err := state.ensureConnected(); err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	localPath := getParamString(req.Params, "source", "")
	remotePath := getParamString(req.Params, "path", "")
	basePath := getParamString(req.Params, "base_path", "/documents")
	if localPath == "" || remotePath == "" {
		return response{ID: req.ID, OK: false, Error: "source and path are required"}
	}

	fullRemotePath := resolvePath(basePath, remotePath)
	destinationDir := normalizePath(path.Dir(fullRemotePath))

	_, _, _, err := mtpx.UploadFiles(
		state.dev,
		state.sid,
		[]string{localPath},
		destinationDir,
		false,
		func(_ *os.FileInfo, _ string, cbErr error) error {
			return cbErr
		},
		func(_ *mtpx.ProgressInfo, cbErr error) error {
			return cbErr
		},
	)
	if err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	return response{ID: req.ID, OK: true, Result: map[string]string{"path": fullRemotePath}}
}

func handleDelete(state *bridgeState, req request) response {
	if err := state.ensureConnected(); err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	basePath := getParamString(req.Params, "base_path", "/documents")
	target := getParamString(req.Params, "path", "")
	if target == "" {
		return response{ID: req.ID, OK: false, Error: "path is required"}
	}
	fullRemotePath := resolvePath(basePath, target)

	err := mtpx.DeleteFile(
		state.dev,
		state.sid,
		[]mtpx.FileProp{{FullPath: fullRemotePath}},
	)
	if err != nil {
		state.close()
		return response{ID: req.ID, OK: false, Error: err.Error()}
	}

	return response{ID: req.ID, OK: true, Result: map[string]bool{"deleted": true}}
}

func walkBase(
	state *bridgeState,
	basePath string,
	cb func(fi *mtpx.FileInfo, fullPath string) error,
) error {
	_, _, _, err := mtpx.Walk(
		state.dev,
		state.sid,
		basePath,
		true,
		false,
		false,
		func(_ uint32, fi *mtpx.FileInfo, cbErr error) error {
			if cbErr != nil {
				return cbErr
			}
			fullPath := normalizePath(fi.FullPath)
			if fullPath == normalizePath(basePath) {
				return nil
			}
			return cb(fi, fullPath)
		},
	)
	if err == nil {
		return nil
	}

	if normalizePath(basePath) != "/" {
		return walkBase(state, "/", cb)
	}
	return err
}

func getParamString(params map[string]interface{}, key, fallback string) string {
	if params == nil {
		return fallback
	}
	raw, ok := params[key]
	if !ok {
		return fallback
	}
	asString, ok := raw.(string)
	if !ok {
		return fallback
	}
	if strings.TrimSpace(asString) == "" {
		return fallback
	}
	return asString
}

func normalizePath(value string) string {
	if strings.TrimSpace(value) == "" {
		return "/"
	}
	cleaned := path.Clean(value)
	if !strings.HasPrefix(cleaned, "/") {
		cleaned = "/" + cleaned
	}
	return cleaned
}

func resolvePath(basePath, remotePath string) string {
	if strings.HasPrefix(remotePath, "/") {
		return normalizePath(remotePath)
	}
	return normalizePath(path.Join(basePath, remotePath))
}

func locateDownloadedFile(tempDir, remotePath string) (string, error) {
	rel := strings.TrimPrefix(normalizePath(remotePath), "/")
	candidate := filepath.Join(tempDir, filepath.FromSlash(rel))
	if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() {
		return candidate, nil
	}

	baseName := filepath.Base(candidate)
	var fallback string
	walkErr := filepath.Walk(tempDir, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		if info.Name() == baseName {
			fallback = p
			return io.EOF
		}
		return nil
	})
	if walkErr != nil && !errors.Is(walkErr, io.EOF) {
		return "", walkErr
	}
	if fallback != "" {
		return fallback, nil
	}
	return "", fmt.Errorf("downloaded file not found for %s", remotePath)
}

func copyFile(source, destination string) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	return out.Sync()
}
