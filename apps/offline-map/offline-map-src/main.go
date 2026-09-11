// The main package is the entry point for the application.
package main

// Import necessary libraries.
import (
	"archive/zip"
	"bytes"
	"context"
	"embed" // Used for embedding files into the binary.
	"encoding/json"
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"io"
	"io/fs"
	"log"
	"math"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket" // WebSocket library for real-time communication.
)

//go:embed templates/index.html
var indexHTML []byte // Embeds the index.html file into the binary.

//go:embed config/map_sources.json
var mapSourcesJSON []byte // Embeds the map_sources.json file into the binary.

//go:embed static/*
var staticFiles embed.FS

// upgrader is used to upgrade HTTP connections to WebSocket connections.
var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024, // Size of the read buffer.
	WriteBufferSize: 1024, // Size of the write buffer.
}

// Global variables used throughout the application.
var (
	mapSources       map[string]string  // Stores the available map sources.
	downloadCancel   context.CancelFunc // Function to cancel an ongoing download.
	downloading      bool               // Flag to indicate if a download is in progress.
	downloadingMutex sync.Mutex         // Mutex to protect access to the downloading flag.
	cacheDir         *string
	presetsDir       *string
	maxWorkers       *int
	rateLimit        *int
	maxRetries       *int
	httpClient       *http.Client
	presetsMu        sync.Mutex
)

// Tile represents a single map tile with X, Y coordinates and zoom level Z.
type Tile struct {
	X, Y, Z uint32
}

// BoundingBox represents a geographical area with North, South, East, and West boundaries.
type BoundingBox struct {
	North, South, East, West float64
}

// LatLng represents a geographical point with latitude and longitude.
type LatLng struct {
	Lat float64 `json:"lat"` // Latitude
	Lng float64 `json:"lng"` // Longitude
}

// DownloadRequest represents a request to download map tiles for a specific area.
type DownloadRequest struct {
	Polygons      [][]LatLng `json:"polygons"`        // The polygons defining the download area.
	MinZoom       int        `json:"min_zoom"`        // The minimum zoom level to download.
	MaxZoom       int        `json:"max_zoom"`        // The maximum zoom level to download.
	MapStyle      string     `json:"map_style"`       // The URL of the map tile server.
	ConvertTo8Bit bool       `json:"convert_to_8bit"` // Whether to convert images to 8-bit PNG.
	BufferKm      float64    `json:"buffer_km"`       // Extra buffer in km to expand each polygon.
	Collection    string     `json:"collection"`      // Named map collection (subfolder under cacheDir).
}

// WorldDownloadRequest represents a request to download map tiles for the entire world.
type WorldDownloadRequest struct {
	MapStyle      string `json:"map_style"`       // The URL of the map tile server.
	ConvertTo8Bit bool   `json:"convert_to_8bit"` // Whether to convert images to 8-bit PNG.
	Collection    string `json:"collection"`      // Named map collection.
	MaxZoom       int    `json:"max_zoom"`        // Maximum zoom level (0-7 recommended).
}

// WSMessage represents a WebSocket message with a type and data.
type WSMessage struct {
	Type string      `json:"type"` // The type of the message (e.g., "start_download").
	Data interface{} `json:"data"` // The data associated with the message.
}

// main is the entry point of the application.
func main() {
	// Command line flags
	port := flag.Int("port", 8080, "Port number for the server")
	cacheDir = flag.String("maps-directory", "maps", "Directory for storing map tiles. This is where the downloaded tiles will be saved.")
	presetsDir = flag.String("presets-directory", "", "Directory for storing presets (defaults to a 'presets' folder next to maps-directory)")
	maxWorkers = flag.Int("max-workers", 50, "Number of concurrent download workers")
	rateLimit = flag.Int("rate-limit", 50, "Maximum number of tiles to download per second")
	maxRetries = flag.Int("max-retries", 5, "Maximum number of retries for downloading a tile")
	logFile := flag.String("log-file", "", "Write logs to this file instead of stdout (e.g. /var/log/offline-map.log)")
	quiet := flag.Bool("quiet", false, "Suppress all log output")
	help := flag.Bool("help", false, "Show help message")

	flag.Parse()

	if *help {
		flag.Usage()
		return
	}

	// HTTP client with a connection pool sized to the worker count.
	httpClient = &http.Client{
		Transport: &http.Transport{
			MaxIdleConnsPerHost:   *maxWorkers,
			MaxConnsPerHost:       *maxWorkers,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   10 * time.Second,
			ResponseHeaderTimeout: 30 * time.Second,
			DisableKeepAlives:     false,
		},
		Timeout: 60 * time.Second,
	}

	// Configure logging.
	if *quiet {
		log.SetOutput(io.Discard)
	} else if *logFile != "" {
		f, err := os.OpenFile(*logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			log.Fatalf("Failed to open log file: %v", err)
		}
		log.SetOutput(f)
	}

	// Create cache directory if it doesn't exist.
	if err := os.MkdirAll(*cacheDir, 0755); err != nil {
		log.Fatalf("Failed to create cache directory: %v", err)
	}

	// Resolve presets directory (default: sibling of maps-directory named "presets").
	if *presetsDir == "" {
		*presetsDir = filepath.Join(filepath.Dir(*cacheDir), "presets")
	}
	if err := os.MkdirAll(*presetsDir, 0755); err != nil {
		log.Fatalf("Failed to create presets directory: %v", err)
	}

	// Load map sources from the embedded JSON file.
	if err := json.Unmarshal(mapSourcesJSON, &mapSources); err != nil {
		log.Fatalf("Failed to load map sources: %v", err)
	}

	// Register HTTP handlers for different routes.
	http.HandleFunc("/favicon.ico", func(w http.ResponseWriter, r *http.Request) {
		r.URL.Path = "/static/favicon.ico"
		http.FileServer(http.FS(staticFiles)).ServeHTTP(w, r)
	})
	http.HandleFunc("/", serveHome)
	http.HandleFunc("/get_map_sources", getMapSources)
	http.HandleFunc("/ws", wsHandler)

	http.HandleFunc("/tiles/", serveTile)
	http.HandleFunc("/get_cached_tiles/", getCachedTiles)
	http.HandleFunc("/get_collections", getCollections)
	http.HandleFunc("/collection_info", collectionInfo)
	http.HandleFunc("/create_collection", createCollection)
	http.HandleFunc("/delete_collection", deleteCollection)
	http.HandleFunc("/download_zip", downloadZip)
	http.HandleFunc("/estimate_tiles", estimateTiles)
	http.HandleFunc("/get_presets", getPresets)
	http.HandleFunc("/save_preset", savePreset)
	http.HandleFunc("/delete_preset", deletePreset)
	http.HandleFunc("/clear_404_cache", clear404Cache)

	staticFS, err := fs.Sub(staticFiles, "static")
	if err != nil {
		log.Fatal(err)
	}
	http.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.FS(staticFS))))

	// Start the HTTP server on port 8080.
	addr := fmt.Sprintf(":%d", *port)
	log.Printf("Starting server on %s", addr)
	if err := http.ListenAndServe(addr, nil); err != nil {
		log.Fatalf("Error starting server: %v", err)
	}
}

// serveHome serves the main HTML page.
func serveHome(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html")
	if _, err := w.Write(indexHTML); err != nil {
		log.Printf("Could not write response: %v", err)
	}
}

// getMapSources serves the available map sources as JSON.
func getMapSources(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if _, err := w.Write(mapSourcesJSON); err != nil {
		log.Printf("Could not write response: %v", err)
	}
}

// wsHandler handles WebSocket connections.
func wsHandler(w http.ResponseWriter, r *http.Request) {
	// Upgrade the HTTP connection to a WebSocket connection.
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Println(err)
		return
	}
	defer func() {
		if err := conn.Close(); err != nil {
			log.Printf("Could not close websocket connection: %v", err)
		}
	}()

	// Loop to read messages from the WebSocket connection.
	for {
		messageType, p, err := conn.ReadMessage()
		if err != nil {
			log.Println(err)
			return
		}
		if messageType == websocket.TextMessage {
			var msg WSMessage
			if err := json.Unmarshal(p, &msg); err != nil {
				log.Println("Error unmarshalling message:", err)
				continue
			}

			// Handle different message types.
			switch msg.Type {
			case "start_download":
				var req DownloadRequest
				b, _ := json.Marshal(msg.Data)
				if err := json.Unmarshal(b, &req); err != nil {
					sendError(conn, "Invalid download request")
					continue
				}
				go handleStartDownload(conn, req)
			case "start_world_download":
				var req WorldDownloadRequest
				b, _ := json.Marshal(msg.Data)
				if err := json.Unmarshal(b, &req); err != nil {
					sendError(conn, "Invalid world download request")
					continue
				}
				go handleStartWorldDownload(conn, req)
			case "cancel_download":
				handleCancelDownload(conn)
			}
		}
	}
}

// handleStartDownload starts a new download process for a defined area.
func handleStartDownload(conn *websocket.Conn, req DownloadRequest) {
	// Lock the mutex to ensure only one download runs at a time.
	downloadingMutex.Lock()
	if downloading {
		sendError(conn, "Another download is already in progress.")
		downloadingMutex.Unlock()
		return
	}
	downloading = true
	downloadingMutex.Unlock()

	log.Printf("Starting download for area: %v, zoom: %d-%d, map style: %s", req.Polygons, req.MinZoom, req.MaxZoom, req.MapStyle)

	// Create a new context to allow for cancellation.
	var ctx context.Context
	ctx, downloadCancel = context.WithCancel(context.Background())

	// Get the style name and cache directory.
	styleName := getStyleName(req.MapStyle)
	styleCacheDir := getStyleCacheDir(req.Collection, styleName)

	// Validate the zoom range.
	if req.MinZoom < 0 || req.MaxZoom > 19 || req.MinZoom > req.MaxZoom {
		sendError(conn, "Invalid zoom range (must be 0-19, min <= max)")
		return
	}
	// Validate the polygons.
	if len(req.Polygons) == 0 {
		sendError(conn, "No polygons provided")
		return
	}

	// Get the list of tiles to download.
	tilesToDownload := getTilesForPolygons(req.Polygons, req.MinZoom, req.MaxZoom, req.BufferKm)

	downloadTiles(ctx, conn, tilesToDownload, req.MapStyle, styleCacheDir, req.ConvertTo8Bit)

	downloadingMutex.Lock()
	downloading = false
	downloadingMutex.Unlock()

	if ctx.Err() == nil {
		sendMessage(conn, "download_complete", nil)
	} else {
		sendMessage(conn, "download_cancelled", nil)
	}
}

// handleStartWorldDownload starts a new download process for the entire world.
func handleStartWorldDownload(conn *websocket.Conn, req WorldDownloadRequest) {
	// Lock the mutex to ensure only one download runs at a time.
	downloadingMutex.Lock()
	if downloading {
		sendError(conn, "Another download is already in progress.")
		downloadingMutex.Unlock()
		return
	}
	downloading = true
	downloadingMutex.Unlock()

	log.Printf("Starting world download, map style: %s", req.MapStyle)

	// Create a new context to allow for cancellation.
	var ctx context.Context
	ctx, downloadCancel = context.WithCancel(context.Background())

	// Get the style name and cache directory.
	styleName := getStyleName(req.MapStyle)
	styleCacheDir := getStyleCacheDir(req.Collection, styleName)

	maxZoom := req.MaxZoom
	if maxZoom < 0 || maxZoom > 19 {
		maxZoom = 7
	}
	tilesToDownload := getWorldTiles(maxZoom)

	downloadTiles(ctx, conn, tilesToDownload, req.MapStyle, styleCacheDir, req.ConvertTo8Bit)

	downloadingMutex.Lock()
	downloading = false
	downloadingMutex.Unlock()

	if ctx.Err() == nil {
		sendMessage(conn, "download_complete", nil)
	} else {
		sendMessage(conn, "download_cancelled", nil)
	}
}

// handleCancelDownload cancels an ongoing download.
func handleCancelDownload(conn *websocket.Conn) {
	if downloadCancel != nil {
		downloadCancel()
		log.Printf("Download cancelled by user")
		sendMessage(conn, "download_cancelled", nil)
	}
}

// load404Cache reads the 404 cache file for a style directory into a set.
func load404Cache(styleCacheDir string) map[string]struct{} {
	cache := make(map[string]struct{})
	data, err := os.ReadFile(filepath.Join(styleCacheDir, "404_tiles.txt"))
	if err != nil {
		return cache
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			cache[line] = struct{}{}
		}
	}
	return cache
}

// record404Tile appends a tile key to the 404 cache file (thread-safe via mu).
func record404Tile(styleCacheDir string, tile Tile, mu *sync.Mutex) {
	key := fmt.Sprintf("%d/%d/%d", tile.Z, tile.X, tile.Y)
	mu.Lock()
	defer mu.Unlock()
	f, err := os.OpenFile(filepath.Join(styleCacheDir, "404_tiles.txt"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintln(f, key)
}

// downloadTiles downloads a list of tiles concurrently.
func downloadTiles(ctx context.Context, conn *websocket.Conn, tilesToDownload []Tile, mapStyle, styleCacheDir string, convertTo8Bit bool) {
	// Load 404 cache so previously missing tiles are skipped without HTTP requests.
	notFoundCache := load404Cache(styleCacheDir)
	var notFoundMu sync.Mutex
	log.Printf("404 cache: %d tiles loaded", len(notFoundCache))

	// Pre-scan: split into already-cached and missing/broken tiles.
	var toDownload []Tile
	preSkipped := 0
	for _, tile := range tilesToDownload {
		key := fmt.Sprintf("%d/%d/%d", tile.Z, tile.X, tile.Y)
		if _, is404 := notFoundCache[key]; is404 {
			preSkipped++
			continue
		}
		tilePath := filepath.Join(styleCacheDir, fmt.Sprintf("%d/%d/%d.png", tile.Z, tile.X, tile.Y))
		if info, err := os.Stat(tilePath); err == nil && info.Size() > 0 {
			preSkipped++
		} else {
			toDownload = append(toDownload, tile)
		}
	}
	log.Printf("Pre-scan: %d cached/404-skipped, %d to download", preSkipped, len(toDownload))

	// Create a channel for WebSocket messages.
	msgChan := make(chan WSMessage)
	var writerWg sync.WaitGroup
	writerWg.Add(1)
	// Start a goroutine to send messages from the channel to the WebSocket connection.
	go func() {
		defer writerWg.Done()
		for msg := range msgChan {
			if err := conn.WriteJSON(msg); err != nil {
				log.Println("Error writing JSON to websocket:", err)
				return
			}
		}
	}()

	msgChan <- WSMessage{Type: "download_started", Data: map[string]int{"total_tiles": len(tilesToDownload)}}
	msgChan <- WSMessage{Type: "prescan_complete", Data: map[string]int{
		"skipped":     preSkipped,
		"to_download": len(toDownload),
	}}

	// Adaptive rate: track interval in nanoseconds and completed count.
	maxInterval := int64(time.Second) / int64(*rateLimit)
	currentInterval := maxInterval
	var completedCount int64
	adaptiveDone := make(chan struct{})

	// Concurrency limiter: mutex+cond allows reducing the cap even while all workers are busy.
	var concMu sync.Mutex
	concCond := sync.NewCond(&concMu)
	concTarget := *maxWorkers
	concActive := 0

	acquireSlot := func() bool {
		concMu.Lock()
		defer concMu.Unlock()
		for concActive >= concTarget {
			concCond.Wait()
			select {
			case <-ctx.Done():
				return false
			default:
			}
		}
		concActive++
		return true
	}
	releaseSlot := func() {
		concMu.Lock()
		concActive--
		concCond.Broadcast()
		concMu.Unlock()
	}

	// Use a WaitGroup to wait for all download goroutines to finish.
	var downloadWg sync.WaitGroup
	tileChan := make(chan Tile, *maxWorkers*2)

	// Start the download workers.
	for i := 0; i < *maxWorkers; i++ {
		downloadWg.Add(1)
		go func() {
			defer downloadWg.Done()
			for tile := range tileChan {
				if !acquireSlot() {
					return
				}
				downloadTile(ctx, msgChan, tile, mapStyle, styleCacheDir, convertTo8Bit, *maxRetries, &completedCount, notFoundCache, &notFoundMu)
				releaseSlot()
			}
		}()
	}

	// Adaptive adjuster: every 2s reduce concurrency+rate when throttled, recover only after sustained good cycles.
	go func() {
		var lastCount int64
		goodCycles := 0
		for {
			select {
			case <-ctx.Done():
				return
			case <-adaptiveDone:
				return
			case <-time.After(2 * time.Second):
			}
			current := atomic.LoadInt64(&completedCount)
			actualRate := float64(current-lastCount) / 2.0
			lastCount = current
			targetRate := float64(time.Second) / float64(currentInterval)
			if actualRate < targetRate*0.75 {
				goodCycles = 0
				// Throttled: slow down feed rate by 40%.
				newInterval := int64(float64(currentInterval) * 1.4)
				if newInterval > int64(time.Second) {
					newInterval = int64(time.Second)
				}
				currentInterval = newInterval
				// Reduce concurrency by ~30%, floor at 1.
				concMu.Lock()
				concTarget -= concTarget / 3
				if concTarget < 1 {
					concTarget = 1
				}
				concMu.Unlock()
				concCond.Broadcast()
				log.Printf("Throttle detected: actual=%.1f/s, reducing to %.1f/s, concurrency=%d",
					actualRate, float64(time.Second)/float64(newInterval), concTarget)
				select {
				case msgChan <- WSMessage{Type: "status", Data: fmt.Sprintf("Throttled — %.0f tiles/s, %d workers", float64(time.Second)/float64(newInterval), concTarget)}:
				case <-adaptiveDone:
					return
				}
			} else if actualRate >= targetRate*0.9 {
				goodCycles++
				// Only start recovering after 3 consecutive good cycles (~6s of stable throughput).
				if goodCycles < 3 {
					continue
				}
				if currentInterval > maxInterval {
					newInterval := int64(float64(currentInterval) * 0.9)
					if newInterval < maxInterval {
						newInterval = maxInterval
					}
					currentInterval = newInterval
				}
				concMu.Lock()
				if concTarget < *maxWorkers {
					concTarget++
					concCond.Broadcast()
				}
				concMu.Unlock()
			} else {
				goodCycles = 0
			}
		}
	}()

DownloadLoop:
	for _, tile := range toDownload {
		timer := time.NewTimer(time.Duration(currentInterval))
		select {
		case <-ctx.Done():
			timer.Stop()
			break DownloadLoop
		case <-timer.C:
			select {
			case tileChan <- tile:
			case <-ctx.Done():
				break DownloadLoop
			}
		}
	}
	close(tileChan)

	// Wait for all downloads to complete, then stop the adaptive goroutine before closing msgChan.
	downloadWg.Wait()
	close(adaptiveDone)
	close(msgChan)
	writerWg.Wait()

	// If the download was not cancelled, send a completion message.
	if ctx.Err() == nil {
		log.Printf("Download finished successfully")
		sendMessage(conn, "tiles_downloaded", nil)
	} else {
		log.Printf("Download failed or was cancelled")
	}
}

// sleepBackoff waits 500ms × (attempt+1), capped at 1s, respecting context cancellation.
func sleepBackoff(ctx context.Context, attempt int) {
	d := time.Duration(attempt+1) * 500 * time.Millisecond
	if d > time.Second {
		d = time.Second
	}
	select {
	case <-time.After(d):
	case <-ctx.Done():
	}
}

// downloadTile downloads a single map tile.
func downloadTile(ctx context.Context, msgChan chan<- WSMessage, tile Tile, mapStyle, styleCacheDir string, convertTo8Bit bool, maxRetries int, completed *int64, notFoundCache map[string]struct{}, notFoundMu *sync.Mutex) {
	// Construct the path to the tile file.
	tileDir := filepath.Join(styleCacheDir, fmt.Sprintf("%d/%d", tile.Z, tile.X))
	tilePath := filepath.Join(tileDir, fmt.Sprintf("%d.png", tile.Y))

	// Skip only if the tile exists and has content (size > 0).
	if info, err := os.Stat(tilePath); err == nil && info.Size() > 0 {
		bounds := tileBounds(tile)
		msgChan <- WSMessage{Type: "tile_skipped", Data: map[string]float64{
			"west":  bounds.West,
			"south": bounds.South,
			"east":  bounds.East,
			"north": bounds.North,
		}}
		return
	}

	// Construct the URL for the tile.
	subdomain := []string{"a", "b", "c"}[rand.Intn(3)]
	url := strings.ReplaceAll(mapStyle, "{s}", subdomain)
	url = strings.ReplaceAll(url, "{z}", fmt.Sprintf("%d", tile.Z))
	url = strings.ReplaceAll(url, "{x}", fmt.Sprintf("%d", tile.X))
	url = strings.ReplaceAll(url, "{y}", fmt.Sprintf("%d", tile.Y))

	var err error
	for attempt := 0; attempt < maxRetries; attempt++ {
		select {
		case <-ctx.Done(): // Check for cancellation.
			return
		default:
		}

		var req *http.Request
		req, err = http.NewRequestWithContext(ctx, "GET", url, nil)
		if err != nil {
			log.Printf("Error creating request for tile %v: %v. Retrying...", tile, err)
			time.Sleep(time.Second * time.Duration(math.Pow(2, float64(attempt))))
			continue
		}
		req.Header.Set("User-Agent", "MapTileDownloader/1.0 (Go)")

		resp, err := httpClient.Do(req)
		if err != nil {
			log.Printf("Error downloading tile %v: %v. Retrying...", tile, err)
			sleepBackoff(ctx, attempt)
			continue
		}

		if resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusServiceUnavailable {
			retryAfter := 2 * time.Second
			if ra := resp.Header.Get("Retry-After"); ra != "" {
				if secs, err2 := time.ParseDuration(ra + "s"); err2 == nil {
					retryAfter = secs
				}
			}
			resp.Body.Close()
			log.Printf("Server throttle (%d) for tile %v, waiting %v", resp.StatusCode, tile, retryAfter)
			select {
			case <-time.After(retryAfter):
			case <-ctx.Done():
				return
			}
			continue
		}

		if resp.StatusCode == http.StatusNotFound {
			resp.Body.Close()
			log.Printf("Tile %v not found (404), skipping.", tile)
			record404Tile(styleCacheDir, tile, notFoundMu)
			atomic.AddInt64(completed, 1)
			bounds := tileBounds(tile)
			msgChan <- WSMessage{Type: "tile_skipped", Data: map[string]float64{
				"west":  bounds.West,
				"south": bounds.South,
				"east":  bounds.East,
				"north": bounds.North,
			}}
			return
		}

		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			log.Printf("Unexpected status code %d for tile %v. Retrying...", resp.StatusCode, tile)
			sleepBackoff(ctx, attempt)
			continue
		}

		body, err := io.ReadAll(resp.Body)
		if err := resp.Body.Close(); err != nil {
			log.Printf("Could not close response body: %v", err)
		}
		if err != nil {
			log.Printf("Error reading tile body for tile %v: %v. Retrying...", tile, err)
			sleepBackoff(ctx, attempt)
			continue
		}

		if err := os.MkdirAll(tileDir, 0755); err != nil {
			log.Printf("Error creating tile directory for tile %v: %v", tile, err)
			return // No point in retrying if we can't create the directory
		}

		// Convert the image to 8-bit PNG if requested.
		if convertTo8Bit {
			img, _, err := image.Decode(bytes.NewReader(body))
			if err == nil {
				paletted := image.NewPaletted(img.Bounds(), color.Palette{})
				draw.Draw(paletted, paletted.Rect, img, img.Bounds().Min, draw.Src)
				var buf bytes.Buffer
				if err := png.Encode(&buf, paletted); err == nil {
					body = buf.Bytes()
				}
			}
		}

		if err := os.WriteFile(tilePath, body, 0644); err != nil {
			log.Printf("Error writing tile %v: %v", tile, err)
			return // No point in retrying if we can't write the file
		}

		atomic.AddInt64(completed, 1)
		bounds := tileBounds(tile)
		msgChan <- WSMessage{Type: "tile_downloaded", Data: map[string]float64{
			"west":  bounds.West,
			"south": bounds.South,
			"east":  bounds.East,
			"north": bounds.North,
		}}
		return // Success!
	}

	// If all retries fail, send a failure message.
	log.Printf("Failed to download tile %v after %d attempts.", tile, maxRetries)
	bounds := tileBounds(tile)
	msgChan <- WSMessage{Type: "tile_failed", Data: map[string]float64{
		"west":  bounds.West,
		"south": bounds.South,
		"east":  bounds.East,
		"north": bounds.North,
	}}
}

// expandPolygon pushes each vertex outward from the centroid by bufferDeg degrees.
func expandPolygon(poly []LatLng, bufferDeg float64) []LatLng {
	if bufferDeg == 0 || len(poly) == 0 {
		return poly
	}
	var sumLat, sumLng float64
	for _, p := range poly {
		sumLat += p.Lat
		sumLng += p.Lng
	}
	centLat := sumLat / float64(len(poly))
	centLng := sumLng / float64(len(poly))
	expanded := make([]LatLng, len(poly))
	for i, p := range poly {
		dLat := p.Lat - centLat
		dLng := p.Lng - centLng
		dist := math.Sqrt(dLat*dLat + dLng*dLng)
		if dist == 0 {
			expanded[i] = p
			continue
		}
		expanded[i] = LatLng{Lat: p.Lat + (dLat/dist)*bufferDeg, Lng: p.Lng + (dLng/dist)*bufferDeg}
	}
	return expanded
}

// getTilesForPolygons calculates the tiles needed to cover the given polygons.
func getTilesForPolygons(polygonsData [][]LatLng, minZoom, maxZoom int, bufferKm float64) []Tile {
	bufferDeg := bufferKm / 111.0
	var allTiles []Tile
	tileMap := make(map[Tile]bool)

	for _, polyData := range polygonsData {
		if len(polyData) < 3 {
			continue
		}
		polyData = expandPolygon(polyData, bufferDeg)

		minLat, minLon := 90.0, 180.0
		maxLat, maxLon := -90.0, -180.0
		for _, p := range polyData {
			if p.Lat < minLat {
				minLat = p.Lat
			}
			if p.Lat > maxLat {
				maxLat = p.Lat
			}
			if p.Lng < minLon {
				minLon = p.Lng
			}
			if p.Lng > maxLon {
				maxLon = p.Lng
			}
		}

		for z := minZoom; z <= maxZoom; z++ {
			tlx, tly := latLonToTile(maxLat, minLon, uint32(z))
			brx, bry := latLonToTile(minLat, maxLon, uint32(z))

			for x := tlx; x <= brx; x++ {
				for y := tly; y <= bry; y++ {
					tile := Tile{X: x, Y: y, Z: uint32(z)}
					if _, exists := tileMap[tile]; exists {
						continue
					}

					bounds := tileBounds(tile)

					// Check if the tile is completely inside the polygon
					if polygonContains(polyData, LatLng{Lat: bounds.North, Lng: bounds.West}) &&
						polygonContains(polyData, LatLng{Lat: bounds.North, Lng: bounds.East}) &&
						polygonContains(polyData, LatLng{Lat: bounds.South, Lng: bounds.West}) &&
						polygonContains(polyData, LatLng{Lat: bounds.South, Lng: bounds.East}) {
						allTiles = append(allTiles, tile)
						tileMap[tile] = true
						continue
					}

					// Check if the polygon is completely inside the tile
					polyInTile := true
					for _, p := range polyData {
						if !tileContains(bounds, p) {
							polyInTile = false
							break
						}
					}
					if polyInTile {
						allTiles = append(allTiles, tile)
						tileMap[tile] = true
						continue
					}

					// Check for intersection
					if polygonIntersects(polyData, bounds) {
						allTiles = append(allTiles, tile)
						tileMap[tile] = true
					}
				}
			}
		}
	}

	return allTiles
}

// tileContains checks if a tile contains a point.
func tileContains(bounds BoundingBox, point LatLng) bool {
	return point.Lat <= bounds.North && point.Lat >= bounds.South && point.Lng >= bounds.West && point.Lng <= bounds.East
}

// polygonIntersects checks if a polygon intersects with a tile.
func polygonIntersects(poly []LatLng, bounds BoundingBox) bool {
	// Check if any of the polygon's vertices are inside the tile
	for _, p := range poly {
		if tileContains(bounds, p) {
			return true
		}
	}

	// Check if any of the tile's corners are inside the polygon
	if polygonContains(poly, LatLng{Lat: bounds.North, Lng: bounds.West}) ||
		polygonContains(poly, LatLng{Lat: bounds.North, Lng: bounds.East}) ||
		polygonContains(poly, LatLng{Lat: bounds.South, Lng: bounds.West}) ||
		polygonContains(poly, LatLng{Lat: bounds.South, Lng: bounds.East}) {
		return true
	}

	// Check if any of the polygon's edges intersect with the tile's edges
	for i := 0; i < len(poly); i++ {
		p1 := poly[i]
		p2 := poly[(i+1)%len(poly)]

		if lineIntersects(p1, p2, LatLng{Lat: bounds.North, Lng: bounds.West}, LatLng{Lat: bounds.North, Lng: bounds.East}) ||
			lineIntersects(p1, p2, LatLng{Lat: bounds.North, Lng: bounds.East}, LatLng{Lat: bounds.South, Lng: bounds.East}) ||
			lineIntersects(p1, p2, LatLng{Lat: bounds.South, Lng: bounds.East}, LatLng{Lat: bounds.South, Lng: bounds.West}) ||
			lineIntersects(p1, p2, LatLng{Lat: bounds.South, Lng: bounds.West}, LatLng{Lat: bounds.North, Lng: bounds.West}) {
			return true
		}
	}

	return false
}

// lineIntersects checks if two line segments intersect.
func lineIntersects(p1, q1, p2, q2 LatLng) bool {
	o1 := orientation(p1, q1, p2)
	o2 := orientation(p1, q1, q2)
	o3 := orientation(p2, q2, p1)
	o4 := orientation(p2, q2, q1)

	if o1 != o2 && o3 != o4 {
		return true
	}

	// Special Cases for colinear points
	if o1 == 0 && onSegment(p1, p2, q1) {
		return true
	}
	if o2 == 0 && onSegment(p1, q2, q1) {
		return true
	}
	if o3 == 0 && onSegment(p2, p1, q2) {
		return true
	}
	if o4 == 0 && onSegment(p2, q1, q2) {
		return true
	}

	return false
}

// orientation finds the orientation of the ordered triplet (p, q, r).
func orientation(p, q, r LatLng) int {
	val := (q.Lng-p.Lng)*(r.Lat-q.Lat) - (q.Lat-p.Lat)*(r.Lng-q.Lng)
	if val == 0 {
		return 0 // Collinear
	}
	if val > 0 {
		return 1 // Clockwise
	}
	return 2 // Counterclockwise
}

// onSegment checks if point q lies on segment pr.
func onSegment(p, q, r LatLng) bool {
	if q.Lat <= math.Max(p.Lat, r.Lat) && q.Lat >= math.Min(p.Lat, r.Lat) &&
		q.Lng <= math.Max(p.Lng, r.Lng) && q.Lng >= math.Min(p.Lng, r.Lng) {
		return true
	}
	return false
}

// getWorldTiles returns a list of all tiles for the world up to the given zoom level.
func getWorldTiles(maxZoom int) []Tile {
	var worldTiles []Tile
	for z := 0; z <= maxZoom; z++ {
		max := 1 << z
		for x := 0; x < max; x++ {
			for y := 0; y < max; y++ {
				worldTiles = append(worldTiles, Tile{X: uint32(x), Y: uint32(y), Z: uint32(z)})
			}
		}
	}
	return worldTiles
}

// serveTile serves a single cached tile.
// URL format: /tiles/<collection>/<style>/<z>/<x>/<y>.png
func serveTile(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/tiles/"), "/")
	if len(parts) != 5 {
		http.NotFound(w, r)
		return
	}
	collection := parts[0]
	styleName := parts[1]
	z := parts[2]
	x := parts[3]
	y := strings.TrimSuffix(parts[4], ".png")

	tilePath := filepath.Join(*cacheDir, sanitizeStyleName(collection), sanitizeStyleName(styleName), z, x, y+".png")
	http.ServeFile(w, r, tilePath)
}

// getCachedTiles returns a list of cached tiles for a specific collection/style.
// URL format: /get_cached_tiles/<collection>/<style>
func getCachedTiles(w http.ResponseWriter, r *http.Request) {
	parts := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/get_cached_tiles/"), "/", 2)
	if len(parts) != 2 {
		http.Error(w, "invalid path", http.StatusBadRequest)
		return
	}
	styleCacheDir := getStyleCacheDir(parts[0], parts[1])

	var cachedTiles [][3]uint32
	err := filepath.Walk(styleCacheDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(info.Name(), ".png") {
			parts := strings.Split(strings.TrimSuffix(path, ".png"), string(filepath.Separator))
			if len(parts) >= 4 {
				z, zErr := strToUint32(parts[len(parts)-3])
				x, xErr := strToUint32(parts[len(parts)-2])
				y, yErr := strToUint32(parts[len(parts)-1])
				if zErr == nil && xErr == nil && yErr == nil {
					cachedTiles = append(cachedTiles, [3]uint32{z, x, y})
				}
			}
		}
		return nil
	})

	if err != nil {
		http.Error(w, fmt.Sprintf("Error reading cache: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(cachedTiles); err != nil {
		http.Error(w, fmt.Sprintf("Error encoding cached tiles: %v", err), http.StatusInternalServerError)
	}
}

// getCollections returns a list of collection folders in the cache directory.
func getCollections(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(*cacheDir)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]string{})
		return
	}
	var collections []string
	for _, e := range entries {
		if e.IsDir() {
			collections = append(collections, e.Name())
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(collections)
}

// collectionInfo returns the total size and tile count for a collection.
func collectionInfo(w http.ResponseWriter, r *http.Request) {
	name := sanitizeStyleName(r.URL.Query().Get("name"))
	if name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	dir := filepath.Join(*cacheDir, name)
	var totalSize int64
	var tileCount int
	filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		if strings.HasSuffix(info.Name(), ".png") {
			totalSize += info.Size()
			tileCount++
		}
		return nil
	})
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"size_bytes": totalSize,
		"tile_count": tileCount,
	})
}

// createCollection creates a new collection directory.
func createCollection(w http.ResponseWriter, r *http.Request) {
	name := sanitizeStyleName(r.URL.Query().Get("name"))
	if name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	if err := os.MkdirAll(filepath.Join(*cacheDir, name), 0755); err != nil {
		http.Error(w, fmt.Sprintf("failed to create: %v", err), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// deleteCollection removes a collection folder and all its tiles.
func deleteCollection(w http.ResponseWriter, r *http.Request) {
	name := sanitizeStyleName(r.URL.Query().Get("name"))
	if name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	dir := filepath.Join(*cacheDir, name)
	if err := os.RemoveAll(dir); err != nil {
		http.Error(w, fmt.Sprintf("failed to delete: %v", err), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

func clear404Cache(w http.ResponseWriter, r *http.Request) {
	collection := sanitizeStyleName(r.URL.Query().Get("collection"))
	style := sanitizeStyleName(r.URL.Query().Get("style"))
	if collection == "" || style == "" {
		http.Error(w, "missing collection or style", http.StatusBadRequest)
		return
	}
	path := filepath.Join(getStyleCacheDir(collection, style), "404_tiles.txt")
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		http.Error(w, fmt.Sprintf("failed to clear: %v", err), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// getStyleName returns the name of the map style for a given URL.
func getStyleName(mapStyleURL string) string {
	for name, url := range mapSources {
		if url == mapStyleURL {
			return name
		}
	}
	return "default"
}

// getStyleCacheDir returns the cache directory for a given collection and style name.
func getStyleCacheDir(collection, styleName string) string {
	return filepath.Join(*cacheDir, sanitizeStyleName(collection), sanitizeStyleName(styleName))
}

// nonAlphanumeric is a regular expression to match any character that is not a letter, number, hyphen, or underscore.
var nonAlphanumeric = regexp.MustCompile(`[^a-zA-Z0-9-_]+`)

// sanitizeStyleName sanitizes the style name to be used as a directory name.
func sanitizeStyleName(styleName string) string {
	return nonAlphanumeric.ReplaceAllString(strings.ReplaceAll(styleName, " ", "-"), "")
}

// sendMessage sends a WebSocket message.
func sendMessage(conn *websocket.Conn, msgType string, data interface{}) {
	msg := WSMessage{Type: msgType, Data: data}
	if err := conn.WriteJSON(msg); err != nil {
		log.Println("Error sending message:", err)
	}
}

// sendError sends an error message over the WebSocket connection.
func sendError(conn *websocket.Conn, message string) {
	sendMessage(conn, "error", map[string]string{"message": message})
}

// strToUint32 converts a string to a uint32.
func strToUint32(s string) (uint32, error) {
	var i uint32
	_, err := fmt.Sscanf(s, "%d", &i)
	return i, err
}

// latLonToTile converts latitude and longitude to tile coordinates.
func latLonToTile(lat, lon float64, zoom uint32) (x, y uint32) {
	latRad := lat * math.Pi / 180
	n := math.Pow(2, float64(zoom))
	x = uint32(n * ((lon + 180) / 360))
	y = uint32(n * (1 - (math.Log(math.Tan(latRad)+1/math.Cos(latRad)) / math.Pi)) / 2)
	return
}

// tileBounds calculates the geographical bounding box of a tile.
func tileBounds(tile Tile) BoundingBox {
	n := math.Pow(2.0, float64(tile.Z))
	lonDeg := float64(tile.X)/n*360.0 - 180.0
	latRad := math.Atan(math.Sinh(math.Pi * (1 - 2*float64(tile.Y)/n)))
	latDeg := latRad * 180.0 / math.Pi

	lon2Deg := float64(tile.X+1)/n*360.0 - 180.0
	lat2Rad := math.Atan(math.Sinh(math.Pi * (1 - 2*float64(tile.Y+1)/n)))
	lat2Deg := lat2Rad * 180.0 / math.Pi

	return BoundingBox{
		North: latDeg,
		South: lat2Deg,
		East:  lon2Deg,
		West:  lonDeg,
	}
}

// polygonContains checks if a point is inside a polygon using the ray casting algorithm.
func polygonContains(poly []LatLng, point LatLng) bool {
	in := false
	for i, j := 0, len(poly)-1; i < len(poly); j, i = i, i+1 {
		if (poly[i].Lat > point.Lat) != (poly[j].Lat > point.Lat) &&
			(point.Lng < (poly[j].Lng-poly[i].Lng)*(point.Lat-poly[i].Lat)/(poly[j].Lat-poly[i].Lat)+poly[i].Lng) {
			in = !in
		}
	}
	return in
}

func presetsFilePath() string {
	return filepath.Join(*presetsDir, "presets.json")
}

func readPresetsFile() (map[string]json.RawMessage, error) {
	presetsMu.Lock()
	defer presetsMu.Unlock()
	data, err := os.ReadFile(presetsFilePath())
	if os.IsNotExist(err) {
		return map[string]json.RawMessage{}, nil
	}
	if err != nil {
		return nil, err
	}
	var presets map[string]json.RawMessage
	if err := json.Unmarshal(data, &presets); err != nil {
		return nil, err
	}
	return presets, nil
}

func writePresetsFile(presets map[string]json.RawMessage) error {
	presetsMu.Lock()
	defer presetsMu.Unlock()
	data, err := json.MarshalIndent(presets, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(presetsFilePath(), data, 0644)
}

func estimateTiles(w http.ResponseWriter, r *http.Request) {
	var req DownloadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}
	tiles := getTilesForPolygons(req.Polygons, req.MinZoom, req.MaxZoom, req.BufferKm)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"count": len(tiles)})
}

func getPresets(w http.ResponseWriter, r *http.Request) {
	presets, err := readPresetsFile()
	if err != nil {
		http.Error(w, "Failed to read presets", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(presets)
}

func savePreset(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	if name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	var body json.RawMessage
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return
	}
	presets, err := readPresetsFile()
	if err != nil {
		http.Error(w, "Failed to read presets", http.StatusInternalServerError)
		return
	}
	presets[name] = body
	if err := writePresetsFile(presets); err != nil {
		http.Error(w, "Failed to save preset", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

func deletePreset(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	if name == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}
	presets, err := readPresetsFile()
	if err != nil {
		http.Error(w, "Failed to read presets", http.StatusInternalServerError)
		return
	}
	delete(presets, name)
	if err := writePresetsFile(presets); err != nil {
		http.Error(w, "Failed to save presets", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// downloadZip streams a ZIP of a collection's cached tiles to the browser.
// Query param: collection=<name>, omit for all collections.
func downloadZip(w http.ResponseWriter, r *http.Request) {
	collection := r.URL.Query().Get("collection")

	var rootDir, zipName string
	if collection == "" || collection == "all" {
		rootDir = *cacheDir
		zipName = "map-tiles-all.zip"
	} else {
		rootDir = filepath.Join(*cacheDir, sanitizeStyleName(collection))
		zipName = fmt.Sprintf("map-tiles-%s.zip", sanitizeStyleName(collection))
	}

	if _, err := os.Stat(rootDir); os.IsNotExist(err) {
		http.Error(w, "No cached tiles found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, zipName))

	zw := zip.NewWriter(w)
	defer func() {
		if err := zw.Close(); err != nil {
			log.Printf("Error closing zip writer: %v", err)
		}
	}()

	err := filepath.Walk(rootDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		relPath, err := filepath.Rel(*cacheDir, path)
		if err != nil {
			return err
		}
		f, err := zw.Create(relPath)
		if err != nil {
			return err
		}
		src, err := os.Open(path)
		if err != nil {
			return err
		}
		defer src.Close()
		_, err = io.Copy(f, src)
		return err
	})
	if err != nil {
		log.Printf("Error building zip: %v", err)
	}
}
